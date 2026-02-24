"""
Returns Module - Webhook Endpoints

Webhooks are API endpoints that EXTERNAL systems call to notify us about events.
Unlike regular APIs (where our frontend calls us), webhooks are called by:
    - Logistics partners (Delhivery, Ecom Express, BlueDart)
    - Refund service (payment processed, failed)

HOW LOGISTICS WEBHOOKS WORK IN REAL LIFE:
1. We approve a return and assign a logistics partner
2. We share our webhook URL with them: POST /api/v1/returns/webhook/pickup/
3. When delivery boy picks up the item, their system calls our webhook
4. We update the return status and notify the customer

SECURITY:
In production, webhooks are secured with:
    - Webhook secret/token in headers
    - IP whitelisting (only allow requests from logistics partner IPs)
    - Request signature verification (HMAC)
For this project, we use a simple token-based check.
"""

import logging
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ReturnRequest, ReturnStatusHistory

logger = logging.getLogger('returns')

# In production, this would come from settings/env
WEBHOOK_SECRET = 'webhook-secret-token-123'


# ============================================================
# LOGISTICS PICKUP WEBHOOK
# ============================================================

@api_view(['POST'])
def logistics_pickup_webhook(request):
    """
    POST /api/v1/returns/webhook/pickup/

    Called by logistics partners when pickup status changes.

    Expected payload from logistics partner:
    {
        "return_number": "RET-0AC15F3F",
        "tracking_number": "DEL123456789",
        "event": "picked_up",          # picked_up, failed_attempt, rescheduled, out_for_pickup
        "event_timestamp": "2026-02-24T10:30:00+05:30",
        "logistics_partner": "Delhivery",
        "delivery_agent": "Ramesh K",
        "remarks": "Picked up successfully",
        "webhook_token": "webhook-secret-token-123"
    }
    """

    data = request.data

    # --- Step 1: Validate webhook token ---
    token = data.get('webhook_token') or request.headers.get('X-Webhook-Token')
    if token != WEBHOOK_SECRET:
        logger.warning(f"Webhook authentication failed. Token: {token}")
        return Response(
            {'error': 'Invalid webhook token'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # --- Step 2: Validate required fields ---
    required_fields = ['return_number', 'event']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return Response(
            {'error': f'Missing required fields: {", ".join(missing)}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # --- Step 3: Find the return request ---
    return_number = data['return_number']
    try:
        return_request = ReturnRequest.objects.get(return_number=return_number)
    except ReturnRequest.DoesNotExist:
        logger.error(f"Webhook: Return not found: {return_number}")
        return Response(
            {'error': f'Return request not found: {return_number}'},
            status=status.HTTP_404_NOT_FOUND
        )

    # --- Step 4: Process the event ---
    event = data['event']
    old_status = return_request.status

    # Map logistics events to our internal statuses
    event_status_map = {
        'out_for_pickup': 'pickup_scheduled',
        'picked_up': 'picked_up',
        'failed_attempt': return_request.status,  # Status doesn't change on failure
        'rescheduled': 'pickup_scheduled',
        'warehouse_received': 'warehouse_received',
        'quality_check_started': 'quality_check',
    }

    new_status = event_status_map.get(event)
    if not new_status:
        return Response(
            {'error': f'Unknown event type: {event}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Update return request
    return_request.status = new_status

    if data.get('tracking_number'):
        return_request.tracking_number = data['tracking_number']
    if data.get('logistics_partner'):
        return_request.logistics_partner = data['logistics_partner']

    # Set pickup dates based on event
    if event == 'picked_up':
        return_request.pickup_completed_date = timezone.now()
    elif event == 'out_for_pickup':
        return_request.pickup_scheduled_date = timezone.now()

    return_request.save()

    # --- Step 5: Record in status history ---
    remarks = data.get('remarks', '')
    agent = data.get('delivery_agent', '')
    comment = f"Logistics event: {event}"
    if agent:
        comment += f" | Agent: {agent}"
    if remarks:
        comment += f" | {remarks}"

    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status=old_status,
        to_status=new_status,
        changed_by='webhook',
        comment=comment,
    )

    logger.info(
        f"Webhook processed: {return_number} | {event} | "
        f"{old_status} → {new_status}"
    )

    # --- Step 6: Return success ---
    # Logistics partners expect a simple success response
    return Response({
        'status': 'success',
        'message': f'Event "{event}" processed for {return_number}',
        'return_number': return_number,
        'new_status': new_status,
    })


# ============================================================
# REFUND WEBHOOK
# ============================================================

@api_view(['POST'])
def refund_status_webhook(request):
    """
    POST /api/v1/returns/webhook/refund/

    Called by the Refund Service when refund status changes.

    Expected payload:
    {
        "return_number": "RET-0AC15F3F",
        "refund_status": "completed",       # initiated, completed, failed
        "refund_reference": "REF-TXN-123456",
        "refund_amount": 79999.00,
        "webhook_token": "webhook-secret-token-123"
    }
    """

    data = request.data

    # Validate token
    token = data.get('webhook_token') or request.headers.get('X-Webhook-Token')
    if token != WEBHOOK_SECRET:
        return Response(
            {'error': 'Invalid webhook token'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Validate required fields
    required_fields = ['return_number', 'refund_status']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return Response(
            {'error': f'Missing required fields: {", ".join(missing)}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Find return request
    try:
        return_request = ReturnRequest.objects.get(
            return_number=data['return_number']
        )
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    old_status = return_request.status
    refund_status = data['refund_status']

    # Map refund events to internal statuses
    refund_status_map = {
        'initiated': 'refund_initiated',
        'completed': 'refund_completed',
        'failed': return_request.status,  # Don't change status on failure
    }

    new_status = refund_status_map.get(refund_status)
    if not new_status:
        return Response(
            {'error': f'Unknown refund status: {refund_status}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Update return request
    return_request.status = new_status
    if data.get('refund_reference'):
        return_request.refund_reference = data['refund_reference']
    if data.get('refund_amount'):
        return_request.refund_amount = data['refund_amount']
    return_request.save()

    # Record history
    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status=old_status,
        to_status=new_status,
        changed_by='webhook',
        comment=f'Refund {refund_status}. Reference: {data.get("refund_reference", "N/A")}',
    )

    logger.info(f"Refund webhook: {data['return_number']} | {refund_status}")

    return Response({
        'status': 'success',
        'message': f'Refund status updated to {new_status}',
        'return_number': data['return_number'],
        'new_status': new_status,
    })