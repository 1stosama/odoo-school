# -*- coding: utf-8 -*-
import logging
import requests

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    """
    Extend POS order to:
    1. Link order to a student
    2. Trigger WhatsApp notification after payment
    """
    _inherit = 'pos.order'

    student_id = fields.Many2one(
        comodel_name='school.student',
        string='Student',
        readonly=True,
        index=True,
    )
    student_card_uid = fields.Char(
        string='Scanned Card UID',
        readonly=True,
    )
    cafeteria_payment = fields.Boolean(
        string='Paid via Student Wallet',
        default=False,
        readonly=True,
    )

    @api.model
    def process_cafeteria_payment(self, card_uid, product_ids, total_amount, pos_order_ref):
        """
        Single RPC method called by POS JS at payment confirmation.
        Validates AND deducts in one atomic call.
        Returns: {'success': bool, 'message': str, 'new_balance': float, 'student_name': str}
        """
        student = self.env['school.student'].search([
            ('card_uid', '=', card_uid),
            ('card_active', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not student:
            return {'success': False, 'message': 'Student card not found or deactivated.'}

        wallet = student.wallet_id[:1]
        if not wallet:
            return {'success': False, 'message': 'No wallet found for this student.'}

        # Balance check
        if wallet.balance < total_amount:
            return {
                'success': False,
                'message': 'Insufficient balance. Available: {:.2f} EGP, Required: {:.2f} EGP'.format(
                    wallet.balance, total_amount),
            }

        # Daily limit check
        effective_limit = 0.0
        if student.plan_active and student.plan_id.daily_limit > 0:
            effective_limit = student.plan_id.daily_limit
        elif student.daily_limit > 0:
            effective_limit = student.daily_limit

        if effective_limit > 0 and (student.today_spent + total_amount) > effective_limit:
            return {
                'success': False,
                'message': 'Daily limit exceeded. Spent: {:.2f} EGP, Limit: {:.2f} EGP'.format(
                    student.today_spent, effective_limit),
            }

        # Time window check
        from datetime import datetime
        now = datetime.now()
        current_hour = now.hour + now.minute / 60.0
        if not (student.purchase_time_from <= current_hour <= student.purchase_time_to):
            return {
                'success': False,
                'message': 'Purchases not allowed at this time. Allowed: {:.0f}:00 - {:.0f}:00'.format(
                    student.purchase_time_from, student.purchase_time_to),
            }

        # Forbidden products check
        if student.forbidden_product_ids and product_ids:
            forbidden_set = set(student.forbidden_product_ids.ids)
            for pid in product_ids:
                if pid in forbidden_set:
                    product = self.env['product.product'].browse(pid)
                    return {
                        'success': False,
                        'message': 'Product "{}" is restricted for this student.'.format(product.name),
                    }

        # Category restriction
        allowed_category_ids = []
        if student.plan_active and student.plan_id.allowed_category_ids:
            allowed_category_ids = student.plan_id.allowed_category_ids.ids
        elif student.allowed_category_ids:
            allowed_category_ids = student.allowed_category_ids.ids

        if allowed_category_ids and product_ids:
            products = self.env['product.product'].browse(product_ids)
            for product in products:
                if product.categ_id.id not in allowed_category_ids:
                    return {
                        'success': False,
                        'message': '"{}" is not in this student\'s meal plan.'.format(product.name),
                    }

        # All checks passed — deduct with row lock
        try:
            wallet._deduct_balance(
                amount=total_amount,
                ref=pos_order_ref or 'POS Purchase',
                cashier_id=self.env.uid,
            )
        except Exception as e:
            return {'success': False, 'message': str(e)}

        # Send notification (non-blocking)
        try:
            _send_purchase_notification_async(student, total_amount, product_ids, self.env)
        except Exception:
            pass

        return {
            'success': True,
            'message': 'OK',
            'new_balance': wallet.balance,
            'student_name': student.name,
        }

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override create to deduct student wallet balance when the order
        is paid with Student Wallet payment method.

        The JS sets student_card_uid on the order when a student card is
        scanned (ProductScreen.scanStudent). This field is serialized via
        PosOrder.serializeForORM patch and sent to the server.

        Flow:
        1. Check if any payment uses "Student Wallet"
        2. If yes, find the student via student_card_uid
        3. Validate all restrictions (balance, daily limit, time, etc.)
        4. Deduct the balance (row-locked)
        5. If any check fails, raise ValidationError to roll back the order
        6. Set cafeteria_payment = True to prevent double deduction
        """
        for vals in vals_list:
            has_wallet = False
            wallet_amount = 0
            for payment_cmd in vals.get('payment_ids') or []:
                if isinstance(payment_cmd, (list, tuple)) and len(payment_cmd) >= 3:
                    pv = payment_cmd[2]
                    if pv.get('payment_method_id'):
                        method = self.env['pos.payment.method'].browse(pv['payment_method_id'])
                        if method.name == "Student Wallet":
                            has_wallet = True
                            wallet_amount += pv.get('amount', 0)

            if not has_wallet:
                continue

            # Skip if already processed by process_cafeteria_payment RPC
            if vals.get('cafeteria_payment'):
                continue

            # Resolve the student — three approaches:
            # 1. student_card_uid sent from JS (code or card UID)
            # 2. partner_id (customer selected in POS) → find student by parent_id
            student = False
            student_card_uid = vals.get('student_card_uid')
            partner_id = vals.get('partner_id')

            if student_card_uid:
                student = self.env['school.student'].search([
                    '|', ('card_uid', '=', student_card_uid),
                         ('student_code', '=', student_card_uid),
                ], limit=1)

            if not student and partner_id:
                partner = self.env['res.partner'].browse(partner_id)
                if partner.exists():
                    # The selected partner might be the student's parent
                    student = self.env['school.student'].search([
                        ('parent_id', '=', partner_id),
                        ('active', '=', True),
                    ], limit=1)
                    # If not found, try matching by name
                    if not student:
                        student = self.env['school.student'].search([
                            ('name', '=', partner.name),
                            ('active', '=', True),
                        ], limit=1)

            if not student:
                raise ValidationError(
                    "Please scan a student card or select a student customer "
                    "before using Student Wallet payment."
                )

            wallet = student.wallet_id[:1]
            if not wallet:
                raise ValidationError("No wallet found for this student.")

            # Extract product IDs from line commands
            product_ids = []
            for line_cmd in vals.get('lines') or []:
                if isinstance(line_cmd, (list, tuple)) and len(line_cmd) >= 3:
                    pid = line_cmd[2].get('product_id')
                    if pid:
                        product_ids.append(pid)

            total_amount = wallet_amount or vals.get('amount_total', 0)

            # Balance check
            if wallet.balance < total_amount:
                raise ValidationError(
                    "Insufficient balance. Available: {:.2f} EGP, Required: {:.2f} EGP".format(
                        wallet.balance, total_amount,
                    )
                )

            # Daily limit check
            effective_limit = 0.0
            if student.plan_active and student.plan_id.daily_limit > 0:
                effective_limit = student.plan_id.daily_limit
            elif student.daily_limit > 0:
                effective_limit = student.daily_limit

            if effective_limit > 0 and (student.today_spent + total_amount) > effective_limit:
                raise ValidationError(
                    "Daily limit exceeded. Spent: {:.2f} EGP, Limit: {:.2f} EGP".format(
                        student.today_spent, effective_limit,
                    )
                )

            # Time window check
            from datetime import datetime
            now = datetime.now()
            current_hour = now.hour + now.minute / 60.0
            if not (student.purchase_time_from <= current_hour <= student.purchase_time_to):
                raise ValidationError(
                    "Purchases not allowed at this time. Allowed: {:.0f}:00 - {:.0f}:00".format(
                        student.purchase_time_from, student.purchase_time_to,
                    )
                )

            # Forbidden products check
            if student.forbidden_product_ids and product_ids:
                forbidden_set = set(student.forbidden_product_ids.ids)
                for pid in product_ids:
                    if pid in forbidden_set:
                        product = self.env['product.product'].browse(pid)
                        raise ValidationError(
                            'Product "{}" is restricted for this student.'.format(product.name)
                        )

            # Category restriction
            allowed_category_ids = []
            if student.plan_active and student.plan_id.allowed_category_ids:
                allowed_category_ids = student.plan_id.allowed_category_ids.ids
            elif student.allowed_category_ids:
                allowed_category_ids = student.allowed_category_ids.ids

            if allowed_category_ids and product_ids:
                products = self.env['product.product'].browse(product_ids)
                for product in products:
                    if product.categ_id.id not in allowed_category_ids:
                        raise ValidationError(
                            '"{}" is not in this student\'s meal plan.'.format(product.name)
                        )

            # All checks passed — deduct with row lock
            try:
                wallet._deduct_balance(
                    amount=total_amount,
                    ref=vals.get('pos_reference') or 'POS Purchase',
                    cashier_id=self.env.uid,
                )
            except Exception as e:
                raise ValidationError("Balance deduction failed: " + str(e))

            # Mark as processed and link student to the order
            vals['cafeteria_payment'] = True
            vals['student_id'] = student.id

            # Send notification (non-blocking)
            try:
                _send_purchase_notification_async(student, total_amount, product_ids, self.env)
            except Exception:
                pass

        return super().create(vals_list)

    def _send_purchase_notification(self):
        """Send notification to parent after purchase."""
        self.ensure_one()
        student = self.student_id
        if not student or student.notification_channel == 'none':
            return

        parent_phone = student.parent_phone
        parent_email = student.parent_id.email if student.parent_id else None
        if not parent_phone and not parent_email:
            _logger.warning('No parent contact for student %s', student.name)
            return

        items = []
        for line in self.lines:
            items.append(f'{line.product_id.name} x{line.qty:.0f}')
        items_str = ' + '.join(items) if items else 'Items'

        wallet = student.wallet_id[:1]
        new_balance = wallet.balance if wallet else 0.0

        message = (
            f'{student.name} purchased at cafeteria:\n'
            f'{items_str}\n'
            f'Amount: {self.amount_total:.2f} EGP\n'
            f'Balance: {new_balance:.2f} EGP'
        )
        subject = f'Cafeteria Purchase - {student.name}'

        self._call_notification_service(
            phone=parent_phone,
            email=parent_email,
            subject=subject,
            message=message,
            channel=student.notification_channel,
        )

    @api.model
    def _send_email(self, recipient_email, subject, body):
        """Send email using Odoo's built-in mail system."""
        if not recipient_email:
            return False
        try:
            mail_values = {
                'subject': subject,
                'body_html': f'<pre style="font-family: sans-serif; font-size: 14px;">{body}</pre>',
                'email_to': recipient_email,
                'auto_delete': True,
            }
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.send()
            _logger.info('Email sent to %s: %s', recipient_email, subject)
            return True
        except Exception:
            _logger.warning('Failed to send email to %s', recipient_email, exc_info=True)
            return False

    @api.model
    def _call_notification_service(self, phone=None, email=None, subject='', message='', channel='email'):
        """
        Send notification to parent.
        Primary: email via Odoo's mail system (always works in Community).
        Enhancement: WhatsApp/SMS via external micro-service (configure separately).
        """
        # Always try email if we have an address
        if email:
            self._send_email(email, subject, message)

        # For whatsapp/sms, also try the external micro-service
        if channel in ('whatsapp', 'sms') and phone:
            service_url = self.env['ir.config_parameter'].sudo().get_param(
                'cafeteria.notification.service.url', ''
            ).strip()
            service_token = self.env['ir.config_parameter'].sudo().get_param(
                'cafeteria.notification.service.token', ''
            ).strip()

            if not service_url:
                return

            try:
                response = requests.post(
                    f'{service_url}/notify',
                    json={
                        'phone': phone,
                        'message': message,
                        'channel': channel,
                    },
                    headers={
                        'Authorization': f'Bearer {service_token}',
                        'Content-Type': 'application/json',
                    },
                    timeout=5,
                )
                if response.status_code != 200:
                    _logger.warning(
                        'Notification service returned %s: %s',
                        response.status_code, response.text,
                    )
            except requests.exceptions.Timeout:
                _logger.warning('Notification service timed out.')
            except requests.exceptions.ConnectionError:
                _logger.warning('Cannot reach notification service.')
            except Exception:
                _logger.warning('Notification service call failed.', exc_info=True)


def _send_purchase_notification_async(student, total_amount, product_ids, env):
    """Send notification to parent after purchase. Called after successful deduction."""
    if student.notification_channel == 'none':
        return
    parent_phone = student.parent_phone
    parent_email = student.parent_id.email if student.parent_id else None
    if not parent_phone and not parent_email:
        return

    products = env['product.product'].browse(product_ids)
    items_str = ' + '.join(products.mapped('name')) if products else 'Items'

    wallet = student.wallet_id[:1]
    new_balance = wallet.balance if wallet else 0.0

    subject = f'Cafeteria Purchase - {student.name}'
    message = (
        f'{student.name} purchased at cafeteria:\n'
        f'{items_str}\n'
        f'Amount: {total_amount:.2f} EGP\n'
        f'Balance: {new_balance:.2f} EGP'
    )

    # Primary: email via Odoo built-in
    if parent_email:
        try:
            mail_values = {
                'subject': subject,
                'body_html': f'<pre style="font-family: sans-serif; font-size: 14px;">{message}</pre>',
                'email_to': parent_email,
                'auto_delete': True,
            }
            mail = env['mail.mail'].sudo().create(mail_values)
            mail.send()
        except Exception:
            _logger.warning('Failed to send email to %s', parent_email, exc_info=True)

    # Enhancement: external micro-service for whatsapp/sms
    if student.notification_channel in ('whatsapp', 'sms') and parent_phone:
        service_url = env['ir.config_parameter'].sudo().get_param(
            'cafeteria.notification.service.url', ''
        )
        service_token = env['ir.config_parameter'].sudo().get_param(
            'cafeteria.notification.service.token', ''
        )
        if service_url:
            try:
                requests.post(
                    '{}/notify'.format(service_url),
                    json={
                        'phone': parent_phone,
                        'message': message,
                        'channel': student.notification_channel,
                    },
                    headers={'Authorization': 'Bearer {}'.format(service_token)},
                    timeout=4,
                )
            except Exception as e:
                _logger.warning('Notification failed: %s', e)


class SchoolStudent(models.Model):
    """Add notification methods to school.student."""
    _inherit = 'school.student'

    def _send_recharge_notification(self, credit_amount, plan=None):
        """Notify parent when wallet is recharged."""
        parent_phone = self.parent_phone
        parent_email = self.parent_id.email if self.parent_id else None
        if self.notification_channel == 'none':
            return
        if not parent_phone and not parent_email:
            return

        if plan:
            subject = f'Meal Plan Activated - {self.name}'
            message = (
                f'Meal plan activated for {self.name}\n'
                f'Plan: {plan.name}\n'
                f'Credits: {credit_amount:.2f} EGP\n'
                f'Balance: {self.balance:.2f} EGP'
            )
        else:
            subject = f'Cafeteria Wallet Recharged - {self.name}'
            message = (
                f'Cafeteria balance topped up for {self.name}\n'
                f'Amount: +{credit_amount:.2f} EGP\n'
                f'New balance: {self.balance:.2f} EGP'
            )

        self.env['pos.order']._call_notification_service(
            phone=parent_phone,
            email=parent_email,
            subject=subject,
            message=message,
            channel=self.notification_channel,
        )

    def _send_low_balance_notification(self):
        """Send low balance alert. Called by daily cron."""
        threshold = float(self.env['ir.config_parameter'].sudo().get_param(
            'cafeteria.low_balance_threshold', '30'
        ))
        for student in self:
            if student.balance <= threshold and student.balance > 0:
                parent_phone = student.parent_phone
                parent_email = student.parent_id.email if student.parent_id else None
                if student.notification_channel == 'none':
                    continue
                if not parent_phone and not parent_email:
                    continue
                subject = f'Low Cafeteria Balance - {student.name}'
                message = (
                    f'Low balance alert for {student.name}\n'
                    f'Current balance: {student.balance:.2f} EGP.\n'
                    f'Please recharge to avoid service interruption.'
                )
                self.env['pos.order']._call_notification_service(
                    phone=parent_phone,
                    email=parent_email,
                    subject=subject,
                    message=message,
                    channel=student.notification_channel,
                )

    @api.model
    def cron_send_low_balance_alerts(self):
        """Scheduled action: send low balance alerts daily."""
        threshold = float(self.env['ir.config_parameter'].sudo().get_param(
            'cafeteria.low_balance_threshold', '30'
        ))
        students = self.search([
            ('active', '=', True),
            ('notification_channel', '!=', 'none'),
        ])
        low_balance_students = students.filtered(
            lambda s: 0 < s.balance <= threshold
        )
        low_balance_students._send_low_balance_notification()
        _logger.info(
            'Low balance alerts sent for %d students', len(low_balance_students)
        )

    @api.model
    def cron_send_daily_summary(self):
        """Scheduled action: send daily balance summary to opted-in parents."""
        students = self.search([
            ('active', '=', True),
            ('notification_channel', '!=', 'none'),
        ])
        for student in students:
            parent_phone = student.parent_phone
            parent_email = student.parent_id.email if student.parent_id else None
            if not parent_phone and not parent_email:
                continue
            subject = f'Cafeteria Daily Summary - {student.name}'
            message = (
                f'Daily summary for {student.name}\n'
                f'Spent today: {student.today_spent:.2f} EGP\n'
                f'Current balance: {student.balance:.2f} EGP'
            )
            try:
                self.env['pos.order']._call_notification_service(
                    phone=parent_phone,
                    email=parent_email,
                    subject=subject,
                    message=message,
                    channel=student.notification_channel,
                )
            except Exception:
                pass
