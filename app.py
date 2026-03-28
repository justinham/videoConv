import os
import subprocess
import uuid
import json
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['HISTORY_FILE'] = 'history.json'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB max

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
    
    # Build ffmpeg command based on hardware choice
    if hardware == 'cpu':
        video_codec = 'libx264'
    elif hardware == 'mac':
        video_codec = 'h264_videotoolbox'
    elif hardware == 'nvidia':
        video_codec = 'h264_nvenc'
    else:
        return jsonify({'error': 'Invalid hardware option'}), 400
    
    cmd = [
        'ffmpeg',
        '-i', input_path,
        '-r', str(fps),
        '-vf', f'scale={width}:{height}',
        '-c:v', video_codec,
        '-crf', '23',
        '-c:a', 'copy',
        '-y',  # Overwrite output
        output_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        
        if result.returncode != 0:
            # Clean up input
            if os.path.exists(input_path):
                os.remove(input_path)
            return jsonify({'error': f'FFmpeg error: {result.stderr[-500:]}'}), 500
        
        # Clean up input file
        if os.path.exists(input_path):
            os.remove(input_path)
        
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
        
        return jsonify({
            'success': True,
            'output_file': f"{filename}_output.mp4",
            'size': file_size
        })
        
    except subprocess.TimeoutExpired:
        if os.path.exists(input_path):
            os.remove(input_path)
        return jsonify({'error': 'Processing timed out (max 1 hour)'}), 500
    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    safe_filename = secure_filename(filename)
    path = os.path.join(app.config['OUTPUT_FOLDER'], safe_filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True)

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
