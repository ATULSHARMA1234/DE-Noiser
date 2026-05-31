import glob

for filename in glob.glob('web/e2e/*.spec.ts'):
    with open(filename, 'r') as f:
        content = f.read()
    
    content = content.replace("includes('/api/auth/me')", "includes(':8000/auth/me')")
    content = content.replace("includes('/api/')", "includes(':8000/')")
    
    with open(filename, 'w') as f:
        f.write(content)
