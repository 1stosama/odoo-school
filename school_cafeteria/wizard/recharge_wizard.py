# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class RechargeWizard(models.TransientModel):
    """
    Quick recharge wizard launched from the student form.
    Allows admin/accountant to top up a student wallet in one step
    without going through the full recharge request workflow.

    Use case: walk-in parent pays cash at reception, accountant
    immediately tops up balance on the spot.
    """
    _name = 'school.recharge.wizard'
    _description = 'Quick Wallet Recharge Wizard'

    student_id = fields.Many2one(
        comodel_name='school.student',
        string='Student',
        required=True,
        readonly=True,
    )
    current_balance = fields.Float(
        string='Current Balance (EGP)',
        related='student_id.balance',
        readonly=True,
    )

    recharge_type = fields.Selection(
        selection=[
            ('manual', 'Manual Top-Up'),
            ('plan', 'Activate Meal Plan'),
        ],
        string='Type',
        required=True,
        default='manual',
    )
    plan_id = fields.Many2one(
        comodel_name='cafeteria.plan',
        string='Meal Plan',
    )
    payment_amount = fields.Float(
        string='Payment Received (EGP)',
        required=True,
    )
    credit_amount = fields.Float(
        string='Credits to Load (EGP)',
        required=True,
        help='May differ from payment if a plan bonus applies.',
    )
    bonus_amount = fields.Float(
        string='Bonus Credits (EGP)',
        compute='_compute_bonus',
    )
    new_balance_preview = fields.Float(
        string='New Balance After Recharge',
        compute='_compute_new_balance',
    )
    note = fields.Char(string='Note / Reference')

    # ------------------------------------------------------------------ #

    @api.depends('payment_amount', 'credit_amount')
    def _compute_bonus(self):
        for rec in self:
            rec.bonus_amount = max(0.0, rec.credit_amount - rec.payment_amount)

    @api.depends('current_balance', 'credit_amount')
    def _compute_new_balance(self):
        for rec in self:
            rec.new_balance_preview = rec.current_balance + rec.credit_amount

    @api.onchange('plan_id')
    def _onchange_plan(self):
        if self.plan_id:
            self.payment_amount = self.plan_id.price
            self.credit_amount = self.plan_id.credit_amount
            self.recharge_type = 'plan'
        else:
            self.recharge_type = 'manual'

    @api.onchange('payment_amount')
    def _onchange_payment_amount(self):
        if self.recharge_type == 'manual':
            self.credit_amount = self.payment_amount

    @api.onchange('recharge_type')
    def _onchange_recharge_type(self):
        if self.recharge_type == 'manual':
            self.plan_id = False
            self.credit_amount = self.payment_amount

    # ------------------------------------------------------------------ #

    def action_recharge(self):
        """Execute the recharge immediately — no approval workflow."""
        self.ensure_one()
        if self.credit_amount <= 0:
            raise UserError('Credit amount must be greater than zero.')

        wallet = self.student_id.wallet_id[:1]
        if not wallet:
            raise UserError(f'No wallet found for {self.student_id.name}.')

        ref = self.note or f'Quick recharge by {self.env.user.name}'

        wallet._add_credit(
            amount=self.credit_amount,
            ref=ref,
            cashier_id=self.env.uid,
            plan_id=self.plan_id.id if self.plan_id else None,
        )

        # Activate plan on student if applicable
        if self.plan_id and self.recharge_type == 'plan':
            from datetime import date, timedelta
            days = self.plan_id.get_duration_days()
            expiry = date.today() + timedelta(days=days) if days else False
            self.student_id.write({
                'plan_id': self.plan_id.id,
                'plan_expiry': expiry,
            })

        # Notify parent
        self.student_id._send_recharge_notification(self.credit_amount, self.plan_id)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': f'✅ {self.student_id.name}: +{self.credit_amount:.2f} EGP credited. New balance: {self.student_id.balance:.2f} EGP',
                'type': 'success',
                'sticky': False,
            },
        }
