import re
import glob
import os
import pandas as pd
import matplotlib.pyplot as plt

log_files = glob.glob("ffmpeg-*.log")
if not log_files:
    raise FileNotFoundError("No FFmpeg report files found in this directory.")

latest_log = max(log_files, key=os.path.getctime)

with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

pattern = r'(\w+)=([^=]+?)(?=\s+\w+=|$)'

parsed_data = []
started = False

for line in lines:
    line = line.strip()
    if not line:
        continue

    if not started:
        if "frame=" in line:
            started = True
        else:
            continue

    matches = re.findall(pattern, line)
    if not matches:
        continue

    data = {}
    for key, value in matches:
        value = value.strip()
        try:
            if "." in value:
                data[key] = float(value)
            else:
                data[key] = int(value)
        except ValueError:
            data[key] = value

    parsed_data.append(data)

df = pd.DataFrame(parsed_data)

df['bitrate'] = df['bitrate'].str.extract(r'(\d+\.?\d*)').astype(float)

mean_frames = df['fps'].mean()
mean_bitrate = df['bitrate'].mean()
mean_quality = df['q'].mean()

print("Average FPS: ", mean_frames, " Average bitrate: ", mean_bitrate, " Average quality: ", mean_quality)

# Export q and fps with respect to time (using frame number) as JSON files
q_data = df[['frame', 'q']].copy()
q_data.columns = ['time', 'q']
q_data.to_json('static/q_vs_time.json', orient='records', indent=2)

fps_data = df[['frame', 'fps']].copy()
fps_data.columns = ['time', 'fps']
fps_data.to_json('static/fps_vs_time.json', orient='records', indent=2)

