import requests

BASE = 'http://127.0.0.1:8000/api/v1/returns'
TOKEN = 'webhook-secret-token-123'

# Step 1: Create return for order 2 (Nike Shoes)
r = requests.post(f'{BASE}/', json={
    "order_id": 2,
    "reason": "size_issue",
    "reason_description": "Shoes are too tight",
    "refund_method": "wallet",
    "pickup_address": "123 MG Road, Bangalore",
    "pickup_pincode": "560034",
    "idempotency_key": "test-key-002"
})
data = r.json()
return_number = data['return_number']
return_id = data['id']
print(f"STEP 1 - Created: {return_number} | Status: {data['status']} | Amount: {data['refund_amount']}")

# Step 2: Out for pickup
r = requests.post(f'{BASE}/webhook/pickup/', json={
    "return_number": return_number,
    "tracking_number": "DEL987654321",
    "event": "out_for_pickup",
    "logistics_partner": "Delhivery",
    "delivery_agent": "Ramesh Kumar",
    "webhook_token": TOKEN
})
print(f"STEP 2 - Out for pickup: {r.json()['new_status']}")

# Step 3: Picked up
r = requests.post(f'{BASE}/webhook/pickup/', json={
    "return_number": return_number,
    "tracking_number": "DEL987654321",
    "event": "picked_up",
    "logistics_partner": "Delhivery",
    "delivery_agent": "Ramesh Kumar",
    "webhook_token": TOKEN
})
print(f"STEP 3 - Picked up: {r.json()['new_status']}")

# Step 4: Warehouse received
r = requests.post(f'{BASE}/webhook/pickup/', json={
    "return_number": return_number,
    "event": "warehouse_received",
    "webhook_token": TOKEN
})
print(f"STEP 4 - Warehouse: {r.json()['new_status']}")

# Step 5: Refund initiated
r = requests.post(f'{BASE}/webhook/refund/', json={
    "return_number": return_number,
    "refund_status": "initiated",
    "refund_reference": "REF-TXN-998877",
    "refund_amount": 4999.00,
    "webhook_token": TOKEN
})
print(f"STEP 5 - Refund initiated: {r.json()['new_status']}")

# Step 6: Refund completed
r = requests.post(f'{BASE}/webhook/refund/', json={
    "return_number": return_number,
    "refund_status": "completed",
    "refund_reference": "REF-TXN-998877",
    "refund_amount": 4999.00,
    "webhook_token": TOKEN
})
print(f"STEP 6 - Refund completed: {r.json()['new_status']}")

# Step 7: Full timeline
r = requests.get(f'{BASE}/{return_id}/status/')
data = r.json()
print(f"\nFULL TIMELINE for {data['return_number']}:")
print("-" * 80)
for entry in data['timeline']:
    fr = entry['from_status'] or 'NEW'
    print(f"  {entry['created_at']} | {fr:20s} -> {entry['to_status']:20s} | {entry['changed_by']}")