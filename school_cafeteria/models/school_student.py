# -*- coding: utf-8 -*-
import hashlib
import secrets
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class SchoolStudent(models.Model):
    """
    Core student record. Each student has:
    - Identity: name, code, class, photo, barcode card UID
    - Parent link: res.partner contact (for notifications)
    - Wallet: auto-created on student creation (see school.wallet)
    - Plan: optional meal subscription plan
    - Spending rules: daily limit, forbidden products, time windows
    """
    _name = 'school.student'
    _description = 'School Student'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'
    _rec_name = 'name'

    # ------------------------------------------------------------------ #
    #  Identity                                                            #
    # ------------------------------------------------------------------ #

    name = fields.Char(
        string='Full Name',
        required=True,
        tracking=True,
    )
    student_code = fields.Char(
        string='Student Code',
        required=True,
        copy=False,
        readonly=True,
        default='/',
        help='Auto-generated unique code. Used as barcode value.',
    )
    photo = fields.Image(
        string='Photo',
        max_width=256,
        max_height=256,
    )
    active = fields.Boolean(default=True, tracking=True)

    # --- Academic ---
    grade = fields.Char(string='Grade / Year', tracking=True)
    section = fields.Char(string='Section / Class')
    academic_year = fields.Char(
        string='Academic Year',
        default=lambda self: self._default_academic_year(),
    )

    # --- Card ---
    card_uid = fields.Char(
        string='Card UID / Barcode',
        copy=False,
        index=True,
        help='Barcode printed on student card. Auto-generated, can be replaced if card is lost.',
    )
    card_active = fields.Boolean(
        string='Card Active',
        default=True,
        help='Disable to block a lost card. Reassign card_uid to new card.',
    )

    # ------------------------------------------------------------------ #
    #  Parent / Guardian                                                   #
    # ------------------------------------------------------------------ #

    parent_id = fields.Many2one(
        comodel_name='res.partner',
        string='Primary Parent / Guardian',
        tracking=True,
        help='Used for notifications. Link to existing Odoo contact.',
    )
    parent_phone = fields.Char(
        related='parent_id.phone',
        string='Parent Phone',
        readonly=True,
    )
    whatsapp_opt_in = fields.Boolean(
        string='WhatsApp Notifications',
        default=True,
        help='Parent opted in to receive WhatsApp purchase notifications.',
    )
    notification_channel = fields.Selection(
        selection=[
            ('whatsapp', 'WhatsApp'),
            ('sms', 'SMS'),
            ('email', 'Email'),
            ('none', 'None'),
        ],
        string='Notification Channel',
        default='whatsapp',
    )

    # ------------------------------------------------------------------ #
    #  Wallet (computed from school.wallet)                               #
    # ------------------------------------------------------------------ #

    wallet_id = fields.One2many(
        comodel_name='school.wallet',
        inverse_name='student_id',
        string='Wallet',
    )
    balance = fields.Float(
        string='Cafeteria Balance (EGP)',
        compute='_compute_balance',
        store=False,
        search='_search_balance',
        help='Current prepaid cafeteria balance.',
    )

    def _search_balance(self, operator, operand):
        wallets = self.env['school.wallet'].search([('balance', operator, operand)])
        return [('wallet_id', 'in', wallets.ids)]
    today_spent = fields.Float(
        string='Spent Today (EGP)',
        compute='_compute_today_spent',
        store=False,
    )

    # ------------------------------------------------------------------ #
    #  Meal Plan                                                           #
    # ------------------------------------------------------------------ #

    plan_id = fields.Many2one(
        comodel_name='cafeteria.plan',
        string='Meal Plan',
        tracking=True,
        ondelete='set null',
    )
    plan_expiry = fields.Date(
        string='Plan Expiry',
        tracking=True,
    )
    plan_active = fields.Boolean(
        string='Plan Active',
        compute='_compute_plan_active',
        store=True,
    )

    # ------------------------------------------------------------------ #
    #  Spending Rules                                                      #
    # ------------------------------------------------------------------ #

    daily_limit = fields.Float(
        string='Daily Limit (EGP)',
        default=0.0,
        help='0 = no limit. Parent-set daily maximum spend.',
    )
    forbidden_product_ids = fields.Many2many(
        comodel_name='product.product',
        relation='student_forbidden_product_rel',
        string='Forbidden Products',
        help='These products will be blocked at POS for this student.',
    )
    allowed_category_ids = fields.Many2many(
        comodel_name='product.category',
        relation='student_allowed_category_rel',
        string='Allowed Categories',
        help='Leave empty = all categories allowed.',
    )
    purchase_time_from = fields.Float(
        string='Purchase Allowed From',
        default=7.0,
        help='Hour (24h). e.g. 7.0 = 07:00 AM',
    )
    purchase_time_to = fields.Float(
        string='Purchase Allowed Until',
        default=16.0,
        help='Hour (24h). e.g. 16.0 = 04:00 PM',
    )

    # ------------------------------------------------------------------ #
    #  Defaults / computed                                                 #
    # ------------------------------------------------------------------ #

    @api.model
    def _default_academic_year(self):
        today = date.today()
        if today.month >= 9:
            return f'{today.year}/{today.year + 1}'
        return f'{today.year - 1}/{today.year}'

    @api.depends('wallet_id.balance')
    def _compute_balance(self):
        for student in self:
            wallet = student.wallet_id[:1]
            student.balance = wallet.balance if wallet else 0.0

    def _compute_today_spent(self):
        today = fields.Date.today()
        for student in self:
            transactions = self.env['school.wallet.transaction'].search([
                ('student_id', '=', student.id),
                ('transaction_type', '=', 'purchase'),
                ('state', '=', 'done'),
                ('date', '>=', today),
            ])
            student.today_spent = sum(transactions.mapped('amount'))

    @api.depends('plan_id', 'plan_expiry')
    def _compute_plan_active(self):
        today = fields.Date.today()
        for student in self:
            student.plan_active = bool(
                student.plan_id and
                student.plan_expiry and
                student.plan_expiry >= today
            )

    # ------------------------------------------------------------------ #
    #  ORM overrides                                                       #
    # ------------------------------------------------------------------ #

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('student_code', '/') == '/':
                vals['student_code'] = self._generate_student_code()
            if not vals.get('card_uid'):
                vals['card_uid'] = vals['student_code']
        students = super().create(vals_list)
        # Auto-create wallet for each new student
        for student in students:
            self.env['school.wallet'].create({'student_id': student.id})
        return students

    # ------------------------------------------------------------------ #
    #  Business logic                                                      #
    # ------------------------------------------------------------------ #

    @api.model
    def _generate_student_code(self):
        """Generate sequential student code: STU-00001"""
        last = self.search([], order='id desc', limit=1)
        next_id = (last.id + 1) if last else 1
        return f'STU-{next_id:05d}'

    def action_view_transactions(self):
        """Open this student's wallet transaction history."""
        self.ensure_one()
        wallet = self.wallet_id[:1]
        if wallet:
            return wallet.action_view_transactions()
        return {'type': 'ir.actions.act_window_close'}

    def action_replace_card(self):
        """Replace lost card: disable old UID, generate new one."""
        self.ensure_one()
        new_uid = f'{self.student_code}-{secrets.token_hex(3).upper()}'
        self.write({
            'card_uid': new_uid,
            'card_active': True,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': f'New card UID assigned: {new_uid}. Reprint the student card.',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_print_card(self):
        """Print student ID card with barcode."""
        return self.env.ref('school_cafeteria.action_report_student_card').report_action(self)

    # ------------------------------------------------------------------ #
    #  Purchase validation (called by POS via RPC)                        #
    # ------------------------------------------------------------------ #

    @api.model
    def get_student_by_uid(self, card_uid):
        """
        Called by POS JS when cashier scans a student card.
        Returns student data needed by POS UI.
        Returns False if not found or card disabled.
        """
        student = self.search([
            ('card_uid', '=', card_uid),
            ('card_active', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not student:
            return False

        # Determine effective daily limit
        effective_limit = 0.0
        if student.plan_active and student.plan_id.daily_limit > 0:
            effective_limit = student.plan_id.daily_limit
        elif student.daily_limit > 0:
            effective_limit = student.daily_limit

        # Forbidden product IDs (union of student + plan restrictions)
        forbidden_ids = student.forbidden_product_ids.ids

        # Allowed categories (plan overrides student if set)
        allowed_category_ids = []
        if student.plan_active and student.plan_id.allowed_category_ids:
            allowed_category_ids = student.plan_id.allowed_category_ids.ids
        elif student.allowed_category_ids:
            allowed_category_ids = student.allowed_category_ids.ids

        return {
            'id': student.id,
            'name': student.name,
            'student_code': student.student_code,
            'card_uid': student.card_uid,
            'parent_id': student.parent_id.id if student.parent_id else None,
            'grade': student.grade or '',
            'section': student.section or '',
            'balance': student.balance,
            'today_spent': student.today_spent,
            'daily_limit': effective_limit,
            'remaining_today': max(0, effective_limit - student.today_spent) if effective_limit else None,
            'forbidden_product_ids': forbidden_ids,
            'allowed_category_ids': allowed_category_ids,
            'plan_name': student.plan_id.name if student.plan_active else None,
            'plan_expiry': str(student.plan_expiry) if student.plan_expiry else None,
            'notification_channel': student.notification_channel,
            'parent_phone': student.parent_phone or '',
            'photo': student.photo.decode() if student.photo else None,
        }

    @api.model
    def validate_purchase(self, card_uid, product_ids, total_amount, pos_order_ref):
        """
        Server-side validation before completing a purchase.
        This is the authoritative check — client-side POS checks are UX only.

        Returns: {'allowed': bool, 'reason': str, 'new_balance': float}
        """
        student = self.search([
            ('card_uid', '=', card_uid),
            ('card_active', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not student:
            return {'allowed': False, 'reason': 'Student card not found or deactivated.'}

        wallet = student.wallet_id[:1]
        if not wallet:
            return {'allowed': False, 'reason': 'Student wallet not found.'}

        # 1. Balance check
        if wallet.balance < total_amount:
            return {
                'allowed': False,
                'reason': f'Insufficient balance. Available: {wallet.balance:.2f} EGP, Required: {total_amount:.2f} EGP',
            }

        # 2. Daily limit check
        effective_limit = 0.0
        if student.plan_active and student.plan_id.daily_limit > 0:
            effective_limit = student.plan_id.daily_limit
        elif student.daily_limit > 0:
            effective_limit = student.daily_limit

        if effective_limit > 0:
            if (student.today_spent + total_amount) > effective_limit:
                return {
                    'allowed': False,
                    'reason': f'Daily limit exceeded. Spent today: {student.today_spent:.2f} EGP, Limit: {effective_limit:.2f} EGP',
                }

        # 3. Time window check
        from datetime import datetime
        current_hour = datetime.now().hour + datetime.now().minute / 60.0
        if not (student.purchase_time_from <= current_hour <= student.purchase_time_to):
            return {
                'allowed': False,
                'reason': f'Purchases not allowed at this time. Allowed: {student.purchase_time_from:.0f}:00 - {student.purchase_time_to:.0f}:00',
            }

        # 4. Forbidden products check
        if student.forbidden_product_ids:
            forbidden_set = set(student.forbidden_product_ids.ids)
            for pid in product_ids:
                if pid in forbidden_set:
                    product = self.env['product.product'].browse(pid)
                    return {
                        'allowed': False,
                        'reason': f'Product "{product.name}" is restricted for this student.',
                    }

        # 5. Category restriction (from plan or student rules)
        allowed_category_ids = []
        if student.plan_active and student.plan_id.allowed_category_ids:
            allowed_category_ids = student.plan_id.allowed_category_ids.ids
        elif student.allowed_category_ids:
            allowed_category_ids = student.allowed_category_ids.ids

        if allowed_category_ids:
            products = self.env['product.product'].browse(product_ids)
            for product in products:
                if product.categ_id.id not in allowed_category_ids:
                    return {
                        'allowed': False,
                        'reason': f'Product "{product.name}" is not included in this student\'s meal plan.',
                    }

        # All checks passed — deduct balance (with row-level lock)
        wallet_locked = self.env['school.wallet'].browse(wallet.id)
        wallet_locked._deduct_balance(
            amount=total_amount,
            ref=pos_order_ref,
            cashier_id=self.env.uid,
        )

        return {
            'allowed': True,
            'reason': 'OK',
            'new_balance': wallet_locked.balance,
        }
