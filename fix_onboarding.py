import glob

for filename in glob.glob('web/e2e/*.spec.ts'):
    with open(filename, 'r') as f:
        content = f.read()
    
    if "onboarding_completed" not in content:
        content = content.replace(
            "localStorage.setItem('user', JSON.stringify({id:1,role:'ADMIN'}));",
            "localStorage.setItem('user', JSON.stringify({id:1,role:'ADMIN'}));\n      localStorage.setItem('onboarding_completed', 'true');"
        )
        
        with open(filename, 'w') as f:
            f.write(content)
