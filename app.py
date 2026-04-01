import os
import subprocess
import uuid
import json
import threading
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['HISTORY_FILE'] = 'history.json'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB max

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}

# Job tracking
jobs = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_video_duration(path):
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0', path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    return 0


def load_history():
    if os.path.exists(app.config['HISTORY_FILE']):
        with open(app.config['HISTORY_FILE'], 'r') as f:
            return json.load(f)
    return []

def save_history(history):
    with open(app.config['HISTORY_FILE'], 'w') as f:
        json.dump(history, f)

def add_to_history(entry):
    history = load_history()
    history.insert(0, entry)  # Add newest first
    save_history(history)

def remove_from_history(filename):
    history = load_history()
    history = [h for h in history if h['output_file'] != filename]
    save_history(history)

def clear_history():
    save_history([])

@app.route('/probe', methods=['POST'])
def probe():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Save temp file
    filename = str(uuid.uuid4())
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'mp4'
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename}.{ext}")
    file.save(temp_path)
    
    try:
        # Get video info with ffprobe
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,r_frame_rate,nb_frames',
            '-show_entries', 'format=duration,size',
            '-of', 'json',
            temp_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            return jsonify({'error': 'Failed to probe video'}), 500
        
        import json as json_lib
        data = json_lib.loads(result.stdout)
        
        # Parse streams
        stream = data.get('streams', [{}])[0]
        width = stream.get('width', 0)
        height = stream.get('height', 0)
        fps_str = stream.get('r_frame_rate', '0/1')
        
        # Parse FPS fraction
        if '/' in fps_str:
            num, den = fps_str.split('/')
            fps = round(int(num) / int(den)) if int(den) != 0 else 0
        else:
            fps = int(fps_str)
        
        # Parse format
        fmt = data.get('format', {})
        duration = float(fmt.get('duration', 0))
        size = int(fmt.get('size', 0))
        
        # Clean up temp file
        os.remove(temp_path)
        
        return jsonify({
            'width': width,
            'height': height,
            'fps': fps,
            'duration': round(duration, 2),
            'size': size
        })
        
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    history = load_history()
    return render_template('index.html', history=history)


@app.route('/process', methods=['POST'])
def process():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file uploaded'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No video file selected'}), 400
    
    resolution = request.form.get('resolution')
    fps = request.form.get('fps')
    hardware = request.form.get('hardware')
    
    if not resolution or not fps or not hardware:
        return jsonify({'error': 'Missing parameters'}), 400
    
    try:
        fps = int(fps)
        if fps < 1 or fps > 240:
            return jsonify({'error': 'FPS must be between 1 and 240'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid FPS value'}), 400
    
    width, height = resolution.split('x')
    original_filename = file.filename
    
    # Save uploaded file
    filename = str(uuid.uuid4())
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'mp4'
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename}.{ext}")
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{filename}_output.mp4")
    
    file.save(input_path)
    
    # Get input duration for progress calculation
    input_duration = get_video_duration(input_path)
    
    # Build ffmpeg command based on hardware choice
    if hardware == 'cpu':
        video_codec = 'libx264'
    elif hardware == 'mac':
        video_codec = 'h264_videotoolbox'
    elif hardware == 'nvidia':
        video_codec = 'h264_nvenc'
    else:
        return jsonify({'error': 'Invalid hardware option'}), 400
    
    # Create job
    job_id = filename
    jobs[job_id] = {
        'status': 'processing',
        'progress': 0,
        'message': 'Starting...',
        'output_file': None,
        'error': None
    }
    
    def run_ffmpeg():
        import threading
        import time

        # FFmpeg command with progress output
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-r', str(fps),
            '-vf', f'scale={width}:{height}',
            '-c:v', video_codec,
            '-crf', '23',
            '-c:a', 'copy',
            '-progress', 'pipe:1',  # Output progress to stdout
            '-y',
            output_path
        ]

        # Timeout: 2x video duration, minimum 5 min, maximum 2 hours
        timeout_sec = max(300, min(int(input_duration * 2) + 60, 7200))

        try:
            jobs[job_id]['message'] = 'Processing frames...'

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # stderr draining thread — prevents buffer deadlock
            stderr_lines = []
            def drain_stderr():
                for line in proc.stderr:
                    stderr_lines.append(line)
            stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
            stderr_thread.start()

            # Timeout watchdog thread
            def watchdog():
                start = time.time()
                while time.time() - start < timeout_sec:
                    if proc.poll() is not None:
                        return  # process already finished
                    time.sleep(2)
                # Timed out — kill the process
                proc.kill()
                proc.wait()

            watchdog_thread = threading.Thread(target=watchdog, daemon=True)
            watchdog_thread.start()

            # Parse progress output from stdout
            for line in proc.stdout:
                line = line.strip()
                if line.startswith('out_time_ms='):
                    try:
                        time_ms = int(line.split('=')[1])
                        time_sec = time_ms / 1000000.0
                        if input_duration > 0:
                            progress = min(int((time_sec / input_duration) * 100), 99)
                            jobs[job_id]['progress'] = progress
                    except:
                        pass
                elif line.startswith('progress=end'):
                    jobs[job_id]['progress'] = 100

            proc.wait()
            stderr_thread.join(timeout=2)

            if proc.returncode != 0:
                # Collect final stderr
                stderr_msg = ''.join(stderr_lines)[-500:]
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = f'FFmpeg error (code {proc.returncode}): {stderr_msg}'
            else:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['progress'] = 100
                jobs[job_id]['message'] = 'Complete!'

                # Add to history
                file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                entry = {
                    'output_file': f"{filename}_output.mp4",
                    'original_name': original_filename,
                    'resolution': resolution,
                    'fps': fps,
                    'hardware': hardware,
                    'timestamp': datetime.now().isoformat(),
                    'size': file_size
                }
                add_to_history(entry)
                jobs[job_id]['output_file'] = f"{filename}_output.mp4"

        except Exception as e:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)
        finally:
            # Clean up input file
            if os.path.exists(input_path):
                os.remove(input_path)
    
    # Run in background thread
    thread = threading.Thread(target=run_ffmpeg)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'job_id': job_id,
        'status': 'started'
    })


@app.route('/status/<job_id>')
def status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'output_file': job.get('output_file'),
        'error': job.get('error')
    })


@app.route('/download/<filename>')
def download(filename):
    safe_filename = secure_filename(filename)
    path = os.path.join(app.config['OUTPUT_FOLDER'], safe_filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404

    # Look up the original filename from history
    history = load_history()
    original_name = None
    for entry in history:
        if entry['output_file'] == safe_filename:
            original_name = entry['original_name']
            break

    # Use original name with _processed suffix, or fall back to stored name
    if original_name:
        base, ext = os.path.splitext(original_name)
        download_name = f"{base}_processed.mp4"
    else:
        download_name = safe_filename

    return send_file(path, as_attachment=True, download_name=download_name)

@app.route('/delete/<filename>', methods=['POST'])
def delete(filename):
    safe_filename = secure_filename(filename)
    path = os.path.join(app.config['OUTPUT_FOLDER'], safe_filename)
    
    # Remove from filesystem
    if os.path.exists(path):
        os.remove(path)
    
    # Remove from history
    remove_from_history(safe_filename)
    
    return jsonify({'success': True})

@app.route('/delete-all', methods=['POST'])
def delete_all():
    history = load_history()
    
    # Remove all files from filesystem
    for entry in history:
        path = os.path.join(app.config['OUTPUT_FOLDER'], entry['output_file'])
        if os.path.exists(path):
            os.remove(path)
    
    # Clear history
    clear_history()
    
    return jsonify({'success': True})

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=5002, debug=True)
