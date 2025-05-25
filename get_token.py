import requests

client_id = '24c3r6ncbg6ihrnug1m5sa18jit564'
client_secret = 'd44o5pw354kjb1xyh24078lbd9kyxf'
url = 'https://id.twitch.tv/oauth2/token'

params = {
    'client_id': client_id,
    'client_secret': client_secret,
    'grant_type': 'client_credentials'
}

response = requests.post(url, params=params)
data = response.json()

print("\n🔑 Access Token:")
print(data['access_token'])
print("\n🕓 Expires in (seconds):", data['expires_in'])
