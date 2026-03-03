# Gunicorn configuration file
# This is automatically loaded by gunicorn if present in the root directly.
# It ensures the timeout is 120 seconds even if the Render dashboard UI 
# overrides the 'startCommand' in render.yaml.

timeout = 160 # Increase worker timeout to 160 seconds to allow for long Gemini OCR operations
workers = 2 # Use 2 workers to handle concurrent requests
threads = 4 # Use threads for I/O bound tasks
