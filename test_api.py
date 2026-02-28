import urllib.request, json
req = urllib.request.Request('http://localhost:5000/api/classes', method='POST', headers={'Content-Type': 'application/json'}, data=json.dumps({'name': 'Test Class', 'names_text': 'Alice\nBob'}).encode('utf-8'))
try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except Exception as e:
    print(f'Error: {e}')
