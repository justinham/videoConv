# Video Processor

A Flask web server for changing video resolution and FPS with optional GPU acceleration.

## Features

- 📤 Upload any video file (mp4, mov, avi, mkv, webm, flv)
- 📐 Change resolution using FFmpeg's scale filter
- 🎬 Adjustable FPS (1-240)
- 🖥️ Hardware acceleration options:
  - **CPU** (libx264) — Best compression, slower
  - **Mac GPU** (h264_videotoolbox) — Fast, macOS only
  - **NVIDIA GPU** (h264_nvenc) — Fast, requires NVIDIA card

## Setup

```bash
cd video-processor
pip install -r requirements.txt
```

## Run

```bash
python3.11 app.py
```

Server starts at: http://localhost:5002

## Usage

1. Open http://localhost:5002 in your browser
2. Upload a video file
3. Select target resolution from dropdown
4. Enter desired FPS
5. Choose hardware acceleration method
6. Click "Process Video"
7. Download the processed video when ready

## Notes

- Output files are stored in `outputs/` directory
- Input files are cleaned up after processing
- Max file size: 10GB
- Audio is preserved without re-encoding (`-c:a copy`)
- CRF 23 is used for consistent quality across all hardware options
