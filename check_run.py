import httpx, time

key = 'bWFoZXNobXVsYWd1cmkxM0BnbWFpbC5jb20.fb6d6868825c7c34d8ff'

print("--- Monitoring run_703974db195a ---")
for i in range(15):
    t = httpx.get(f'https://pseudogram-api.onrender.com/v1/simulate/run_703974db195a/truth', headers={'X-API-Key': key}).json()
    st = httpx.get('https://linkplease-dm-service.onrender.com/stats').json()
    print(f"[{i+1}s] Sim Status: {t.get('status')} | Webhook 200s: {t.get('webhook_200_count', 0)} | Live Stats: {st}")
    if t.get('status') == 'complete':
        print("\n=== Simulation Complete! ===")
        print("Final Server Truth:", t)
        print("Final App Stats:", st)
        break
    time.sleep(2)
