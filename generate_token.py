import sys
from denoiser.api.auth import create_access_token

token = create_access_token({"sub": "admin@example.com", "role": "ADMIN"})
print(token)
