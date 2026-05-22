# -*- coding: utf-8 -*-
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class SchoolRechargeRequest(models.Model):
    """
    Workflow: Parent requests balance recharge → Accountant confirms payment
    received → Balance credited to student wallet.

    This keeps us out of fintech territory — the school handles the money,
    we just record and automate the credit step.

    Flow: draft → confirmed → paid → credited
    """
    _name = 'school.recharge.request'
    _description = 'Cafeteria Balance Recharge Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='/',
    )
    student_id = fields.Many2one(
        comodel_name='school.student',
        string='Student',
        required=True,
        tracking=True,
        index=True,
    )
    wallet_id = fields.Many2one(
        comodel_name='school.wallet',
        string='Wallet',
        compute='_compute_wallet_id',
        store=True,
    )

    # --- Plan or manual amount ---
    recharge_type = fields.Selection(
        selection=[
            ('manual', 'Manual Top-Up'),
            ('plan', 'Activate Meal Plan'),
        ],
        string='Type',
        required=True,
        default='manual',
        tracking=True,
    )
    plan_id = fields.Many2one(
        comodel_name='cafeteria.plan',
        string='Meal Plan',
        tracking=True,
        help='Select a plan to activate. Price and credits auto-fill.',
    )

    # --- Amounts ---
    payment_amount = fields.Float(
        string='Payment Amount (EGP)',
        required=True,
        tracking=True,
        help='Amount parent physically pays to school.',
    )
    credit_amount = fields.Float(
        string='Credits to Load (EGP)',
        required=True,
        tracking=True,
        help='Amount credited to student wallet. May be more than payment if plan has bonus.',
    )
    bonus_amount = fields.Float(
        string='Bonus Credits (EGP)',
        compute='_compute_bonus_amount',
        store=True,
    )

    # --- State ---
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('paid', 'Payment Received'),
            ('credited', 'Wallet Credited'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        tracking=True,
        index=True,
    )

    # --- Dates ---
    request_date = fields.Date(
        string='Request Date',
        default=fields.Date.today,
        readonly=True,
    )
    payment_date = fields.Date(string='Payment Date', tracking=True)
    credited_date = fields.Datetime(string='Credited At', readonly=True)

    # --- Plan expiry (computed when plan activated) ---
    plan_expiry = fields.Date(
        string='Plan Expiry',
        compute='_compute_plan_expiry',
        store=True,
    )

    # --- Current balance for reference ---
    current_balance = fields.Float(
        string='Current Balance',
        related='wallet_id.balance',
        readonly=True,
    )

    note = fields.Text(string='Notes')

    # ------------------------------------------------------------------ #
    #  Computed                                                            #
    # ------------------------------------------------------------------ #

    @api.depends('student_id')
    def _compute_wallet_id(self):
        for rec in self:
            wallet = self.env['school.wallet'].search(
                [('student_id', '=', rec.student_id.id)], limit=1
            )
            rec.wallet_id = wallet

    @api.depends('payment_amount', 'credit_amount')
    def _compute_bonus_amount(self):
        for rec in self:
            rec.bonus_amount = max(0, rec.credit_amount - rec.payment_amount)

    @api.depends('plan_id', 'request_date')
    def _compute_plan_expiry(self):
        for rec in self:
            if rec.plan_id:
                days = rec.plan_id.get_duration_days()
                start = rec.request_date or date.today()
                rec.plan_expiry = start + timedelta(days=days) if days else False
            else:
                rec.plan_expiry = False

    # ------------------------------------------------------------------ #
    #  Onchange                                                            #
    # ------------------------------------------------------------------ #

    @api.onchange('plan_id')
    def _onchange_plan_id(self):
        if self.plan_id:
            self.payment_amount = self.plan_id.price
            self.credit_amount = self.plan_id.credit_amount
            self.recharge_type = 'plan'
        else:
            self.recharge_type = 'manual'

    @api.onchange('recharge_type')
    def _onchange_recharge_type(self):
        if self.recharge_type == 'manual':
            self.plan_id = False
            self.credit_amount = self.payment_amount

    @api.onchange('payment_amount')
    def _onchange_payment_amount(self):
        if self.recharge_type == 'manual':
            self.credit_amount = self.payment_amount

    # ------------------------------------------------------------------ #
    #  ORM                                                                 #
    # ------------------------------------------------------------------ #

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'school.recharge.request'
                ) or '/'
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    #  State transitions                                                   #
    # ------------------------------------------------------------------ #

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Only draft requests can be confirmed.')
            rec.state = 'confirmed'

    def action_mark_paid(self):
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError('Only confirmed requests can be marked as paid.')
            rec.write({
                'state': 'paid',
                'payment_date': fields.Date.today(),
            })

    def action_credit_wallet(self):
        """
        The key action: credit student wallet and activate plan if applicable.
        Called by accountant after confirming payment received.
        """
        for rec in self:
            if rec.state != 'paid':
                raise UserError('Request must be in Paid state to credit wallet.')
            if not rec.wallet_id:
                raise UserError(f'No wallet found for student {rec.student_id.name}.')

            # Credit the wallet
            rec.wallet_id._add_credit(
                amount=rec.credit_amount,
                ref=rec.name,
                cashier_id=self.env.uid,
                plan_id=rec.plan_id.id if rec.plan_id else None,
            )

            # Activate plan on student if this is a plan recharge
            if rec.plan_id and rec.plan_expiry:
                rec.student_id.write({
                    'plan_id': rec.plan_id.id,
                    'plan_expiry': rec.plan_expiry,
                })

            rec.write({
                'state': 'credited',
                'credited_date': fields.Datetime.now(),
            })

            # Send notification to parent
            rec.student_id._send_recharge_notification(rec.credit_amount, rec.plan_id)

    def action_cancel(self):
        for rec in self:
            if rec.state == 'credited':
                raise UserError('Cannot cancel a request that has already been credited to the wallet.')
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError('Only cancelled or confirmed requests can be reset.')
            rec.state = 'draft'
