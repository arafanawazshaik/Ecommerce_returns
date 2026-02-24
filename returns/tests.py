"""
Returns Module - Tests

These tests validate the core return workflows:
1. Eligibility checks (return window, order status)
2. Return creation (happy path, validation, idempotency)
3. Status transitions (cancel, webhooks)
4. Fraud detection (high value, frequent returns)

Run tests with: python manage.py test returns
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Order, ReturnRequest, ReturnStatusHistory, FraudFlag


class BaseTestCase(TestCase):
    """
    Base test class with helper methods to create test data.
    All test classes inherit from this.
    """

    def setUp(self):
        """Runs before EVERY test method. Creates fresh test data."""
        self.client = APIClient()
        self.base_url = '/api/v1/returns'

        # Create a delivered electronics order (eligible for return)
        self.order_electronics = Order.objects.create(
            order_number='OD-TEST-001',
            customer_id=1001,
            customer_name='Test User',
            customer_email='test@example.com',
            customer_phone='9876543210',
            product_name='Samsung Galaxy S24',
            product_sku='SAM-S24-128',
            category='electronics',
            quantity=1,
            unit_price=Decimal('79999.00'),
            total_amount=Decimal('79999.00'),
            status='delivered',
            ordered_at=timezone.now() - timedelta(days=5),
            delivered_at=timezone.now() - timedelta(days=2),
            payment_method='upi',
            shipping_address='123 Test Street',
            shipping_pincode='560001',
        )

        # Create a delivered fashion order (low value)
        self.order_fashion = Order.objects.create(
            order_number='OD-TEST-002',
            customer_id=1001,
            customer_name='Test User',
            customer_email='test@example.com',
            product_name='Nike Shoes',
            product_sku='NIKE-42',
            category='fashion',
            quantity=1,
            unit_price=Decimal('4999.00'),
            total_amount=Decimal('4999.00'),
            status='delivered',
            ordered_at=timezone.now() - timedelta(days=10),
            delivered_at=timezone.now() - timedelta(days=7),
            payment_method='credit_card',
            shipping_address='123 Test Street',
            shipping_pincode='560001',
        )

        # Create a shipped order (NOT delivered - should fail eligibility)
        self.order_shipped = Order.objects.create(
            order_number='OD-TEST-003',
            customer_id=1002,
            customer_name='Another User',
            customer_email='another@example.com',
            product_name='Sony Headphones',
            product_sku='SONY-WH',
            category='electronics',
            quantity=1,
            unit_price=Decimal('19999.00'),
            total_amount=Decimal('19999.00'),
            status='shipped',
            ordered_at=timezone.now() - timedelta(days=2),
            payment_method='wallet',
            shipping_address='456 Test Street',
            shipping_pincode='560002',
        )

        # Create an expired order (delivered 60 days ago)
        self.order_expired = Order.objects.create(
            order_number='OD-TEST-004',
            customer_id=1001,
            customer_name='Test User',
            customer_email='test@example.com',
            product_name='Old Book',
            product_sku='BOOK-001',
            category='books',
            quantity=1,
            unit_price=Decimal('500.00'),
            total_amount=Decimal('500.00'),
            status='delivered',
            ordered_at=timezone.now() - timedelta(days=65),
            delivered_at=timezone.now() - timedelta(days=60),
            payment_method='upi',
            shipping_address='123 Test Street',
            shipping_pincode='560001',
        )


# ============================================================
# ELIGIBILITY TESTS
# ============================================================

class EligibilityTests(BaseTestCase):
    """Test return eligibility checks."""

    def test_delivered_order_is_eligible(self):
        """Delivered order within return window should be eligible."""
        response = self.client.post(
            f'{self.base_url}/check-eligibility/',
            {'order_id': self.order_electronics.id},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['eligible'])
        self.assertEqual(response.data['return_window_days'], 10)  # Electronics = 10 days

    def test_fashion_has_30_day_window(self):
        """Fashion category should have 30-day return window."""
        response = self.client.post(
            f'{self.base_url}/check-eligibility/',
            {'order_id': self.order_fashion.id},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['eligible'])
        self.assertEqual(response.data['return_window_days'], 30)

    def test_shipped_order_not_eligible(self):
        """Order that is not delivered should NOT be eligible."""
        response = self.client.post(
            f'{self.base_url}/check-eligibility/',
            {'order_id': self.order_shipped.id},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['eligible'])
        self.assertIn('not delivered', response.data['reason'])

    def test_expired_order_not_eligible(self):
        """Order past return window should NOT be eligible."""
        response = self.client.post(
            f'{self.base_url}/check-eligibility/',
            {'order_id': self.order_expired.id},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['eligible'])
        self.assertIn('expired', response.data['reason'])

    def test_nonexistent_order(self):
        """Checking eligibility for non-existent order should return error."""
        response = self.client.post(
            f'{self.base_url}/check-eligibility/',
            {'order_id': 9999},
            format='json'
        )
        self.assertEqual(response.status_code, 400)


# ============================================================
# RETURN CREATION TESTS
# ============================================================

class ReturnCreationTests(BaseTestCase):
    """Test return request creation."""

    def test_create_return_success(self):
        """Should successfully create a return for eligible order."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'reason_description': 'Too tight',
                'refund_method': 'original',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn('return_number', response.data)
        self.assertEqual(response.data['status'], 'approved')  # Low value = auto-approved
        self.assertEqual(response.data['reason'], 'size_issue')

    def test_create_return_high_value_pending(self):
        """High value return should be set to pending (needs manual review)."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_electronics.id,
                'reason': 'defective',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'pending')  # High value = pending
        self.assertTrue(response.data['is_high_value'])

    def test_create_return_records_status_history(self):
        """Creating a return should record initial status in history."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'wrong_item',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)

        # Check status history was created
        return_id = response.data['id']
        history = ReturnStatusHistory.objects.filter(return_request_id=return_id)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().to_status, 'approved')

    def test_idempotency_prevents_duplicates(self):
        """Same idempotency key should return existing return, not create new."""
        payload = {
            'order_id': self.order_fashion.id,
            'reason': 'size_issue',
            'pickup_address': '123 Test Street',
            'pickup_pincode': '560001',
            'idempotency_key': 'unique-key-123',
        }

        # First request - creates return
        response1 = self.client.post(f'{self.base_url}/', payload, format='json')
        self.assertEqual(response1.status_code, 201)

        # Second request - same key, should return existing
        response2 = self.client.post(f'{self.base_url}/', payload, format='json')
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response1.data['return_number'], response2.data['return_number'])

    def test_invalid_pincode_rejected(self):
        """Pincode must be exactly 6 digits."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '123',  # Invalid - not 6 digits
            },
            format='json'
        )
        self.assertEqual(response.status_code, 400)

    def test_shipped_order_cannot_be_returned(self):
        """Order that's not delivered should be rejected."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_shipped.id,
                'reason': 'defective',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 400)


# ============================================================
# CANCEL RETURN TESTS
# ============================================================

class CancelReturnTests(BaseTestCase):
    """Test return cancellation."""

    def _create_return(self):
        """Helper to create a return for testing."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'changed_mind',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        return response.data['id']

    def test_cancel_approved_return(self):
        """Should be able to cancel an approved return."""
        return_id = self._create_return()
        response = self.client.post(f'{self.base_url}/{return_id}/cancel/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'cancelled')

    def test_cancel_records_history(self):
        """Cancellation should be recorded in status history."""
        return_id = self._create_return()
        self.client.post(f'{self.base_url}/{return_id}/cancel/')

        history = ReturnStatusHistory.objects.filter(
            return_request_id=return_id,
            to_status='cancelled'
        )
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().changed_by, 'customer')

    def test_cannot_cancel_completed_return(self):
        """Should NOT be able to cancel a return that's already picked up."""
        return_id = self._create_return()
        # Manually set status to picked_up
        ReturnRequest.objects.filter(id=return_id).update(status='picked_up')

        response = self.client.post(f'{self.base_url}/{return_id}/cancel/')
        self.assertEqual(response.status_code, 400)


# ============================================================
# WEBHOOK TESTS
# ============================================================

class WebhookTests(BaseTestCase):
    """Test logistics and refund webhooks."""

    def _create_approved_return(self):
        """Helper to create an approved return."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        return response.data['return_number'], response.data['id']

    def test_pickup_webhook_updates_status(self):
        """Logistics webhook should update return status."""
        return_number, return_id = self._create_approved_return()

        response = self.client.post(
            f'{self.base_url}/webhook/pickup/',
            {
                'return_number': return_number,
                'event': 'picked_up',
                'tracking_number': 'DEL123',
                'logistics_partner': 'Delhivery',
                'webhook_token': 'webhook-secret-token-123',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['new_status'], 'picked_up')

    def test_webhook_invalid_token_rejected(self):
        """Webhook with wrong token should be rejected."""
        return_number, _ = self._create_approved_return()

        response = self.client.post(
            f'{self.base_url}/webhook/pickup/',
            {
                'return_number': return_number,
                'event': 'picked_up',
                'webhook_token': 'wrong-token',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 401)

    def test_refund_webhook_completes_return(self):
        """Refund completed webhook should set status to refund_completed."""
        return_number, _ = self._create_approved_return()

        response = self.client.post(
            f'{self.base_url}/webhook/refund/',
            {
                'return_number': return_number,
                'refund_status': 'completed',
                'refund_reference': 'REF-123',
                'refund_amount': 4999.00,
                'webhook_token': 'webhook-secret-token-123',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['new_status'], 'refund_completed')


# ============================================================
# FRAUD FLAG TESTS
# ============================================================

class FraudFlagTests(BaseTestCase):
    """Test rule-based fraud detection."""

    def test_high_value_return_is_flagged(self):
        """Return for Rs.79,999 should create a high_value fraud flag."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_electronics.id,
                'reason': 'defective',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['is_flagged'])
        self.assertTrue(response.data['is_high_value'])

        # Check fraud flag was created in database
        flags = FraudFlag.objects.filter(
            return_request_id=response.data['id'],
            flag_type='high_value'
        )
        self.assertEqual(flags.count(), 1)

    def test_low_value_return_not_flagged_for_high_value(self):
        """Return for Rs.4,999 should NOT create high_value flag."""
        response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data['is_high_value'])

        flags = FraudFlag.objects.filter(
            return_request_id=response.data['id'],
            flag_type='high_value'
        )
        self.assertEqual(flags.count(), 0)


# ============================================================
# LIST & DETAIL TESTS
# ============================================================

class ListAndDetailTests(BaseTestCase):
    """Test listing and detail endpoints."""

    def test_list_returns_empty(self):
        """Should return empty list when no returns exist."""
        response = self.client.get(f'{self.base_url}/list/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_list_returns_with_data(self):
        """Should return returns after creating one."""
        self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        response = self.client.get(f'{self.base_url}/list/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_get_return_detail(self):
        """Should return full details of a return."""
        create_response = self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        return_id = create_response.data['id']

        response = self.client.get(f'{self.base_url}/{return_id}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('order', response.data)
        self.assertIn('status_history', response.data)

    def test_get_nonexistent_return(self):
        """Should return 404 for non-existent return."""
        response = self.client.get(f'{self.base_url}/9999/')
        self.assertEqual(response.status_code, 404)

    def test_filter_by_customer(self):
        """Should filter returns by customer_id."""
        self.client.post(
            f'{self.base_url}/',
            {
                'order_id': self.order_fashion.id,
                'reason': 'size_issue',
                'pickup_address': '123 Test Street',
                'pickup_pincode': '560001',
            },
            format='json'
        )
        response = self.client.get(f'{self.base_url}/list/?customer_id=1001')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        response = self.client.get(f'{self.base_url}/list/?customer_id=9999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)