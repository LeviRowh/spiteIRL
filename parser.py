import re


# Rename the file to match the name of the FFmpeg log file
log_file = "ffmpeg_log.txt"

# Open the file with UTF-16 encoding
with open(log_file, 'r', encoding='utf-16') as f:
    lines = f.readlines()

# Regular expression to match key=value pairs, handling spaces
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
    data = {}
    for key, value in matches:
        # Clean the value: strip spaces
        value = value.strip()
        # Try to convert to number if possible
        try:
            if '.' in value:
                data[key] = float(value)
            else:
                data[key] = int(value)
        except ValueError:
            data[key] = value
    parsed_data.append(data)

# Now, parsed_data is a list of dictionaries
# You can print it or save to CSV, etc.

import pandas as pd

df = pd.DataFrame(parsed_data)
print(df)  # Print first 10 rows