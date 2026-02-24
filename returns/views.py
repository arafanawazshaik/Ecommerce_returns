"""
Returns Module - API Views

These are the actual API endpoints that handle HTTP requests.
Each view receives a request, processes it, and returns a JSON response.

ENDPOINTS:
    POST   /api/v1/returns/                   → Create a return request
    GET    /api/v1/returns/                   → List all returns
    GET    /api/v1/returns/{id}/              → Get return details
    GET    /api/v1/returns/{id}/status/       → Get status history
    POST   /api/v1/returns/{id}/cancel/       → Cancel a return
    POST   /api/v1/returns/check-eligibility/ → Check return eligibility
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Order, ReturnRequest, ReturnStatusHistory, FraudFlag
from .serializers import (
    CreateReturnRequestSerializer,
    ReturnRequestSerializer,
    ReturnRequestListSerializer,
    ReturnStatusHistorySerializer,
    CheckEligibilitySerializer,
)

# Logger for this module (outputs to console + logs/returns.log)
logger = logging.getLogger('returns')


# ============================================================
# CREATE RETURN REQUEST
# ============================================================

@api_view(['POST'])
def create_return(request):
    """
    POST /api/v1/returns/

    Creates a new return request.

    Flow:
    1. Validate input data
    2. Check idempotency (prevent duplicates)
    3. Check eligibility (return window, order status)
    4. Create return request
    5. Record initial status in history
    6. Check fraud flags
    7. Return response
    """

    serializer = CreateReturnRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Validation failed', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    data = serializer.validated_data
    order = Order.objects.get(id=data['order_id'])

    # --- Step 1: Idempotency Check ---
    idempotency_key = data.get('idempotency_key')
    if idempotency_key:
        existing = ReturnRequest.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            logger.info(f"Duplicate return request blocked. Idempotency key: {idempotency_key}")
            return Response(
                ReturnRequestSerializer(existing).data,
                status=status.HTTP_200_OK
            )

    # --- Step 2: Eligibility Check ---
    eligibility = _check_eligibility(order)
    if not eligibility['eligible']:
        return Response(
            {'error': 'Return not eligible', 'reason': eligibility['reason']},
            status=status.HTTP_400_BAD_REQUEST
        )

    # --- Step 3: Create Return Request ---
    return_request = ReturnRequest(
        order=order,
        customer_id=order.customer_id,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        reason=data['reason'],
        reason_description=data.get('reason_description', ''),
        refund_method=data.get('refund_method', 'original'),
        refund_amount=order.total_amount,
        pickup_address=data['pickup_address'],
        pickup_pincode=data['pickup_pincode'],
        idempotency_key=idempotency_key,
    )

    # Check if high value
    high_value_threshold = settings.RETURN_POLICY['HIGH_VALUE_THRESHOLD']
    if order.total_amount >= high_value_threshold:
        return_request.is_high_value = True
        return_request.status = 'pending'  # Needs manual review
        logger.info(f"High value return: {order.total_amount} for order {order.order_number}")
    else:
        return_request.status = 'approved'  # Auto-approve

    return_request.save()

    # --- Step 4: Record Status History ---
    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status='',
        to_status=return_request.status,
        changed_by='system',
        comment=f'Return request created. Reason: {data["reason"]}'
    )

    # --- Step 5: Check Fraud Flags ---
    _check_fraud_flags(return_request, order)

    logger.info(f"Return created: {return_request.return_number} for order {order.order_number}")

    return Response(
        ReturnRequestSerializer(return_request).data,
        status=status.HTTP_201_CREATED
    )


# ============================================================
# LIST RETURNS
# ============================================================

@api_view(['GET'])
def list_returns(request):
    """
    GET /api/v1/returns/

    List all returns with optional filters.

    Query params:
        ?customer_id=123       → Filter by customer
        ?status=approved       → Filter by status
        ?is_flagged=true       → Show only flagged returns
        ?page=1                → Pagination
    """

    queryset = ReturnRequest.objects.select_related('order').all()

    # Apply filters
    customer_id = request.query_params.get('customer_id')
    if customer_id:
        queryset = queryset.filter(customer_id=customer_id)

    status_filter = request.query_params.get('status')
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    is_flagged = request.query_params.get('is_flagged')
    if is_flagged and is_flagged.lower() == 'true':
        queryset = queryset.filter(is_flagged=True)

    # Simple pagination
    page_size = 20
    page = int(request.query_params.get('page', 1))
    start = (page - 1) * page_size
    end = start + page_size

    total_count = queryset.count()
    returns = queryset[start:end]

    serializer = ReturnRequestListSerializer(returns, many=True)

    return Response({
        'count': total_count,
        'page': page,
        'page_size': page_size,
        'results': serializer.data,
    })


# ============================================================
# GET RETURN DETAILS
# ============================================================

@api_view(['GET'])
def get_return_detail(request, return_id):
    """
    GET /api/v1/returns/{id}/

    Get full details of a specific return request,
    including images and status history.
    """

    try:
        return_request = ReturnRequest.objects.select_related('order').prefetch_related(
            'images', 'status_history', 'fraud_flags'
        ).get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = ReturnRequestSerializer(return_request)
    return Response(serializer.data)


# ============================================================
# GET STATUS HISTORY
# ============================================================

@api_view(['GET'])
def get_status_history(request, return_id):
    """
    GET /api/v1/returns/{id}/status/

    Get the timeline of status changes for a return.
    This is the "track your return" feature.
    """

    try:
        return_request = ReturnRequest.objects.get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    history = ReturnStatusHistory.objects.filter(
        return_request=return_request
    ).order_by('created_at')

    serializer = ReturnStatusHistorySerializer(history, many=True)

    return Response({
        'return_number': return_request.return_number,
        'current_status': return_request.status,
        'timeline': serializer.data,
    })


# ============================================================
# CANCEL RETURN
# ============================================================

@api_view(['POST'])
def cancel_return(request, return_id):
    """
    POST /api/v1/returns/{id}/cancel/

    Customer cancels their return request.
    Can only cancel if status is 'pending' or 'approved'.
    """

    try:
        return_request = ReturnRequest.objects.get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Can only cancel in early stages
    cancellable_statuses = ['pending', 'approved', 'pickup_scheduled']
    if return_request.status not in cancellable_statuses:
        return Response(
            {'error': f'Cannot cancel return in "{return_request.status}" status. '
                      f'Cancellation allowed only in: {", ".join(cancellable_statuses)}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    old_status = return_request.status
    return_request.status = 'cancelled'
    return_request.save()

    # Record in history
    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status=old_status,
        to_status='cancelled',
        changed_by='customer',
        comment='Cancelled by customer'
    )

    logger.info(f"Return {return_request.return_number} cancelled by customer")

    return Response({
        'message': 'Return request cancelled successfully',
        'return_number': return_request.return_number,
        'status': 'cancelled',
    })


# ============================================================
# CHECK ELIGIBILITY
# ============================================================

@api_view(['POST'])
def check_eligibility(request):
    """
    POST /api/v1/returns/check-eligibility/

    Check if an order is eligible for return BEFORE creating the request.
    This is called when customer clicks "Return" button in the app.

    Request: {"order_id": 123}
    Response: {"eligible": true, "return_window_days": 7, "days_remaining": 3, ...}
    """

    serializer = CheckEligibilitySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Validation failed', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    order = Order.objects.get(id=serializer.validated_data['order_id'])
    eligibility = _check_eligibility(order)

    return Response(eligibility)


# ============================================================
# PRIVATE HELPER FUNCTIONS
# ============================================================

def _check_eligibility(order):
    """
    Core business logic: Is this order eligible for return?

    Rules:
    1. Order must be delivered
    2. Must be within return window (varies by category)
    3. Must not already have an active return
    """

    policy = settings.RETURN_POLICY

    # Rule 1: Order must be delivered
    if order.status != 'delivered':
        return {
            'eligible': False,
            'reason': f'Order is not delivered yet. Current status: {order.status}',
        }

    # Rule 2: Must be within return window
    if not order.delivered_at:
        return {
            'eligible': False,
            'reason': 'Delivery date not recorded',
        }

    # Get category-specific return window
    category_windows = {
        'electronics': policy['ELECTRONICS_RETURN_WINDOW_DAYS'],
        'fashion': policy['FASHION_RETURN_WINDOW_DAYS'],
    }
    return_window_days = category_windows.get(
        order.category,
        policy['DEFAULT_RETURN_WINDOW_DAYS']
    )

    deadline = order.delivered_at + timedelta(days=return_window_days)
    now = timezone.now()

    if now > deadline:
        days_overdue = (now - deadline).days
        return {
            'eligible': False,
            'reason': f'Return window expired {days_overdue} day(s) ago. '
                      f'Return window for {order.category} is {return_window_days} days.',
            'return_window_days': return_window_days,
            'deadline': deadline.isoformat(),
        }

    # Rule 3: Check for existing active return
    active_statuses = ['pending', 'approved', 'pickup_scheduled', 'picked_up',
                       'warehouse_received', 'quality_check', 'refund_initiated']
    existing_return = ReturnRequest.objects.filter(
        order=order,
        status__in=active_statuses
    ).exists()

    if existing_return:
        return {
            'eligible': False,
            'reason': 'An active return request already exists for this order.',
        }

    # All checks passed
    days_remaining = (deadline - now).days
    return {
        'eligible': True,
        'return_window_days': return_window_days,
        'days_remaining': days_remaining,
        'deadline': deadline.isoformat(),
        'order_number': order.order_number,
        'category': order.category,
        'total_amount': str(order.total_amount),
    }


def _check_fraud_flags(return_request, order):
    """
    Rule-based fraud detection. No ML — just business rules.

    Checks:
    1. Too many returns in 30 days (> MAX_RETURNS_PER_MONTH)
    2. High value return (> HIGH_VALUE_THRESHOLD)
    3. Quick return (within 1 hour of delivery)
    """

    policy = settings.RETURN_POLICY
    flags_created = []

    # --- Flag 1: Frequent returns ---
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_returns_count = ReturnRequest.objects.filter(
        customer_id=return_request.customer_id,
        created_at__gte=thirty_days_ago
    ).count()

    if recent_returns_count > policy['MAX_RETURNS_PER_MONTH']:
        flag = FraudFlag.objects.create(
            return_request=return_request,
            customer_id=return_request.customer_id,
            flag_type='frequent_returns',
            description=f'Customer has {recent_returns_count} returns in the last 30 days. '
                        f'Threshold: {policy["MAX_RETURNS_PER_MONTH"]}'
        )
        flags_created.append(flag)
        logger.warning(f"Fraud flag: frequent_returns for customer {return_request.customer_id}")

    # --- Flag 2: High value return ---
    if order.total_amount >= policy['HIGH_VALUE_THRESHOLD']:
        flag = FraudFlag.objects.create(
            return_request=return_request,
            customer_id=return_request.customer_id,
            flag_type='high_value',
            description=f'Return amount Rs.{order.total_amount} exceeds threshold of '
                        f'Rs.{policy["HIGH_VALUE_THRESHOLD"]}'
        )
        flags_created.append(flag)
        logger.warning(f"Fraud flag: high_value for order {order.order_number}")

    # --- Flag 3: Quick return (within 1 hour of delivery) ---
    if order.delivered_at:
        time_since_delivery = timezone.now() - order.delivered_at
        if time_since_delivery < timedelta(hours=1):
            flag = FraudFlag.objects.create(
                return_request=return_request,
                customer_id=return_request.customer_id,
                flag_type='quick_return',
                description=f'Return requested within {time_since_delivery.seconds // 60} minutes '
                            f'of delivery'
            )
            flags_created.append(flag)
            logger.warning(f"Fraud flag: quick_return for order {order.order_number}")

    # Mark return as flagged if any flags were created
    if flags_created:
        return_request.is_flagged = True
        return_request.save(update_fields=['is_flagged'])

    return flags_created