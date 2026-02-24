"""
Returns Module - API Views
"""

import uuid
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Order, ReturnRequest, ReturnStatusHistory, FraudFlag
from .serializers import (
    ReturnRequestSerializer,
    ReturnRequestListSerializer,
    ReturnStatusHistorySerializer,
    CreateReturnRequestSerializer,
    CheckEligibilitySerializer,
)

logger = logging.getLogger('returns')


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _check_eligibility(order):
    """Check if an order is eligible for return."""

    # Rule 1: Order must be delivered
    if order.status != 'delivered':
        return {
            'eligible': False,
            'reason': f'Order is not delivered yet. Current status: {order.status}',
        }

    # Rule 2: Must be within return window
    policy = settings.RETURN_POLICY
    category = order.category.lower()

    if category == 'electronics':
        window_days = policy['ELECTRONICS_RETURN_WINDOW_DAYS']
    elif category == 'fashion':
        window_days = policy['FASHION_RETURN_WINDOW_DAYS']
    else:
        window_days = policy['DEFAULT_RETURN_WINDOW_DAYS']

    deadline = order.delivered_at + timedelta(days=window_days)
    now = timezone.now()

    if now > deadline:
        return {
            'eligible': False,
            'reason': f'Return window has expired. Deadline was {deadline}.',
        }

    # Rule 3: No existing active return for this order
    active_returns = ReturnRequest.objects.filter(
        order=order,
        status__in=['pending', 'approved', 'pickup_scheduled', 'picked_up'],
    ).exists()

    if active_returns:
        return {
            'eligible': False,
            'reason': 'An active return already exists for this order.',
        }

    days_remaining = (deadline - now).days
    return {
        'eligible': True,
        'return_window_days': window_days,
        'days_remaining': days_remaining,
        'deadline': deadline,
        'order_number': order.order_number,
        'category': order.category,
        'total_amount': str(order.total_amount),
    }


def _check_fraud_flags(return_request, order):
    """Run rule-based fraud checks and create flags if needed."""

    policy = settings.RETURN_POLICY
    flags_created = []

    # Flag 1: Frequent returns (more than threshold in 30 days)
    recent_returns = ReturnRequest.objects.filter(
        customer_id=return_request.customer_id,
        created_at__gte=timezone.now() - timedelta(days=30),
    ).count()

    if recent_returns > policy['MAX_RETURNS_PER_MONTH']:
        flag = FraudFlag.objects.create(
            return_request=return_request,
            customer_id=return_request.customer_id,
            flag_type='frequent_returns',
            description=f'Customer has {recent_returns} returns in the last 30 days (threshold: {policy["MAX_RETURNS_PER_MONTH"]})',
        )
        flags_created.append(flag)
        logger.warning(f'Fraud flag: frequent_returns for customer {return_request.customer_id}')

    # Flag 2: High value return
    if order.total_amount >= policy['HIGH_VALUE_THRESHOLD']:
        flag = FraudFlag.objects.create(
            return_request=return_request,
            customer_id=return_request.customer_id,
            flag_type='high_value',
            description=f'High value return: Rs.{order.total_amount} (threshold: Rs.{policy["HIGH_VALUE_THRESHOLD"]})',
        )
        flags_created.append(flag)
        logger.warning(f'Fraud flag: high_value for order {order.order_number}')

    # Flag 3: Quick return (within 1 hour of delivery)
    if order.delivered_at:
        time_since_delivery = timezone.now() - order.delivered_at
        if time_since_delivery < timedelta(hours=1):
            flag = FraudFlag.objects.create(
                return_request=return_request,
                customer_id=return_request.customer_id,
                flag_type='quick_return',
                description=f'Return requested within {time_since_delivery.total_seconds() / 60:.0f} minutes of delivery',
            )
            flags_created.append(flag)
            logger.warning(f'Fraud flag: quick_return for order {order.order_number}')

    # If any flags were created, mark the return as flagged
    if flags_created:
        return_request.is_flagged = True
        return_request.save()

    return flags_created


# ============================================================
# API ENDPOINTS
# ============================================================

@api_view(['POST'])
def create_return(request):
    """
    POST /api/v1/returns/

    Create a new return request. This is called when customer
    clicks "Return Item" in the app.
    """

    serializer = CreateReturnRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    # Check idempotency key
    idempotency_key = data.get('idempotency_key')
    if idempotency_key:
        existing = ReturnRequest.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            logger.info(f'Duplicate return request blocked. Idempotency key: {idempotency_key}')
            return Response(
                ReturnRequestSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

    # Check eligibility
    order = data['order_id']
    eligibility = _check_eligibility(order)
    if not eligibility['eligible']:
        return Response(
            {'error': eligibility['reason']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Determine if high value
    policy = settings.RETURN_POLICY
    is_high_value = order.total_amount >= policy['HIGH_VALUE_THRESHOLD']

    if is_high_value:
        initial_status = 'pending'
        logger.info(f'High value return: {order.total_amount} for order {order.order_number}')
    else:
        initial_status = 'approved'

    # Create return request
    return_request = ReturnRequest.objects.create(
        return_number=f'RET-{uuid.uuid4().hex[:8].upper()}',
        order=order,
        customer_id=order.customer_id,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        reason=data['reason'],
        reason_description=data.get('reason_description', ''),
        status=initial_status,
        refund_method=data.get('refund_method', 'original'),
        refund_amount=order.total_amount,
        pickup_address=data['pickup_address'],
        pickup_pincode=data['pickup_pincode'],
        is_high_value=is_high_value,
        idempotency_key=idempotency_key or '',
    )

    # Record initial status
    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status='',
        to_status=initial_status,
        changed_by='system',
        comment=f'Return request created. Reason: {data["reason"]}',
    )

    # Run fraud checks
    _check_fraud_flags(return_request, order)

    logger.info(f'Return created: {return_request.return_number} for order {order.order_number}')

    return Response(
        ReturnRequestSerializer(return_request).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET'])
def list_returns(request):
    """
    GET /api/v1/returns/list/

    List return requests with filters and CURSOR-BASED pagination.

    Why cursor-based instead of offset?
    - Offset: "Give me page 5" -> DB skips 80 rows (slow on large tables)
    - Cursor: "Give me rows after ID 100" -> DB uses index (fast always)
    - At scale (millions of returns), offset pagination degrades badly

    Query params:
    - customer_id: Filter by customer
    - status: Filter by status
    - is_flagged: Filter flagged returns (true/false)
    - cursor: ID of last item from previous page (for next page)
    - direction: 'next' (default) or 'prev'
    - page_size: Items per page (default 20, max 100)
    """
    queryset = ReturnRequest.objects.select_related('order').all()

    # Apply filters
    customer_id = request.query_params.get('customer_id')
    status_filter = request.query_params.get('status')
    is_flagged = request.query_params.get('is_flagged')

    if customer_id:
        queryset = queryset.filter(customer_id=customer_id)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if is_flagged is not None:
        queryset = queryset.filter(is_flagged=is_flagged.lower() == 'true')

    # Cursor-based pagination
    page_size = min(int(request.query_params.get('page_size', 20)), 100)
    cursor = request.query_params.get('cursor')
    direction = request.query_params.get('direction', 'next')

    if cursor:
        try:
            cursor_id = int(cursor)
            if direction == 'next':
                queryset = queryset.filter(id__gt=cursor_id)
            else:
                queryset = queryset.filter(id__lt=cursor_id).order_by('-id')
        except ValueError:
            return Response(
                {'error': 'Invalid cursor value'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Order by ID for consistent pagination
    if direction != 'prev':
        queryset = queryset.order_by('id')

    results = list(queryset[:page_size + 1])  # Fetch one extra to check if more exist

    has_more = len(results) > page_size
    results = results[:page_size]

    # If prev direction, reverse to maintain correct order
    if direction == 'prev':
        results.reverse()

    serializer = ReturnRequestListSerializer(results, many=True)

    response_data = {
        'results': serializer.data,
        'page_size': page_size,
        'has_more': has_more,
    }

    # Include cursors for next/prev navigation
    if results:
        response_data['next_cursor'] = results[-1].id
        response_data['prev_cursor'] = results[0].id

    return Response(response_data)


@api_view(['GET'])
def get_return_detail(request, return_id):
    """
    GET /api/v1/returns/{id}/

    Get full details of a specific return request.
    """

    try:
        return_request = ReturnRequest.objects.select_related('order').prefetch_related(
            'images', 'status_history', 'fraud_flags'
        ).get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = ReturnRequestSerializer(return_request)
    return Response(serializer.data)


@api_view(['GET'])
def get_status_history(request, return_id):
    """
    GET /api/v1/returns/{id}/status/

    Get the status timeline for a return request.
    Used for "Track your return" feature.
    """

    try:
        return_request = ReturnRequest.objects.get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND,
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


@api_view(['POST'])
def cancel_return(request, return_id):
    """
    POST /api/v1/returns/{id}/cancel/

    Cancel a return request. Only allowed in certain statuses.
    """

    try:
        return_request = ReturnRequest.objects.get(id=return_id)
    except ReturnRequest.DoesNotExist:
        return Response(
            {'error': 'Return request not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    cancellable_statuses = ['pending', 'approved', 'pickup_scheduled']
    if return_request.status not in cancellable_statuses:
        return Response(
            {'error': f'Cannot cancel return in "{return_request.status}" status. Cancellation allowed only in: {", ".join(cancellable_statuses)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_status = return_request.status
    return_request.status = 'cancelled'
    return_request.save()

    ReturnStatusHistory.objects.create(
        return_request=return_request,
        from_status=old_status,
        to_status='cancelled',
        changed_by='customer',
        comment='Cancelled by customer',
    )

    return Response({
        'message': 'Return request cancelled successfully',
        'return_number': return_request.return_number,
        'status': 'cancelled',
    })


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
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    order = serializer.validated_data['order_id']
    result = _check_eligibility(order)
    return Response(result)