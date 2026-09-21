with open('backend/app.py', 'r') as f:
    lines = f.readlines()

new_lines = []
in_geocode = False
for line in lines:
    if line.startswith("@app.get('/api/geocode')"):
        in_geocode = True
    
    if in_geocode:
        if line.startswith("@app.get('/api/settings')"):
            in_geocode = False
            new_lines.append(line)
    else:
        new_lines.append(line)

content = "".join(new_lines)
content = content.replace("connect-src 'self' https://nominatim.openstreetmap.org https://api.open-meteo.com https://geocoding-api.open-meteo.com https://geocoding.geo.census.gov", "connect-src 'self' https://geocode.arcgis.com https://api.open-meteo.com")

with open('backend/app.py', 'w') as f:
    f.write(content)
