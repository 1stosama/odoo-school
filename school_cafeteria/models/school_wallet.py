# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class SchoolWallet(models.Model):
    """
    One wallet per student. The wallet is just a balance container.
    Actual history lives in school.wallet.transaction records.
    Balance is computed from transactions to ensure consistency.

    Architecture decision: we store a cached `balance` Float field
    (store=True) and update it on every transaction. This avoids
    recomputing the full sum on every POS lookup.
    """
    _name = 'school.wallet'
    _description = 'Student Cafeteria Wallet'
    _rec_name = 'student_id'

    student_id = fields.Many2one(
        comodel_name='school.student',
        string='Student',
        required=True,
        ondelete='cascade',
        index=True,
    )
    balance = fields.Float(
        string='Balance (EGP)',
        default=0.0,
        digits=(10, 2),
        help='Cached balance. Updated on every transaction.',
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.EGP', raise_if_not_found=False)
                             or self.env.company.currency_id,
    )
    transaction_ids = fields.One2many(
        comodel_name='school.wallet.transaction',
        inverse_name='wallet_id',
        string='Transactions',
    )
    transaction_count = fields.Integer(
        string='Transactions',
        compute='_compute_transaction_count',
    )
    last_activity = fields.Datetime(
        string='Last Activity',
        readonly=True,
    )

    def _compute_transaction_count(self):
        for wallet in self:
            wallet.transaction_count = len(wallet.transaction_ids)

    # ------------------------------------------------------------------ #
    #  Core balance operations                                            #
    # ------------------------------------------------------------------ #

    def _add_credit(self, amount, ref='', cashier_id=None, plan_id=None):
        """
        Credit the wallet (recharge or plan activation).
        Creates a transaction record and updates cached balance.
        """
        self.ensure_one()
        if amount <= 0:
            raise ValidationError('Credit amount must be positive.')

        self.env['school.wallet.transaction'].create({
            'wallet_id': self.id,
            'student_id': self.student_id.id,
            'amount': amount,
            'transaction_type': 'credit',
            'reference': ref or 'Manual Recharge',
            'cashier_id': cashier_id or self.env.uid,
            'state': 'done',
            'plan_id': plan_id,
        })
        self.write({
            'balance': self.balance + amount,
            'last_activity': fields.Datetime.now(),
        })

    def _deduct_balance(self, amount, ref='', cashier_id=None):
        """
        Deduct from wallet for a purchase.
        Uses SELECT FOR UPDATE to prevent race conditions when
        two POS terminals process same student simultaneously.
        """
        self.ensure_one()

        # Row-level lock — prevents double deduction
        self.env.cr.execute(
            'SELECT id, balance FROM school_wallet WHERE id = %s FOR UPDATE',
            (self.id,)
        )
        row = self.env.cr.fetchone()
        current_balance = row[1] if row else 0.0

        if current_balance < amount:
            raise UserError(
                f'Insufficient balance. Available: {current_balance:.2f} EGP, '
                f'Required: {amount:.2f} EGP'
            )

        self.env['school.wallet.transaction'].create({
            'wallet_id': self.id,
            'student_id': self.student_id.id,
            'amount': amount,
            'transaction_type': 'purchase',
            'reference': ref or 'POS Purchase',
            'cashier_id': cashier_id or self.env.uid,
            'state': 'done',
        })
        self.env.cr.execute(
            'UPDATE school_wallet SET balance = balance - %s, last_activity = NOW() WHERE id = %s',
            (amount, self.id)
        )
        self.invalidate_recordset(['balance', 'last_activity'])

    def action_view_transactions(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'{self.student_id.name} – Transactions',
            'res_model': 'school.wallet.transaction',
            'view_mode': 'list,form',
            'domain': [('wallet_id', '=', self.id)],
            'context': {'default_wallet_id': self.id},
        }


class SchoolWalletTransaction(models.Model):
    """
    Immutable audit trail for all wallet movements.
    Created by _add_credit() and _deduct_balance() only.
    Never modified after creation.
    """
    _name = 'school.wallet.transaction'
    _description = 'Wallet Transaction'
    _order = 'date desc, id desc'

    wallet_id = fields.Many2one(
        comodel_name='school.wallet',
        string='Wallet',
        required=True,
        ondelete='cascade',
        index=True,
    )
    student_id = fields.Many2one(
        comodel_name='school.student',
        string='Student',
        required=True,
        index=True,
    )
    date = fields.Datetime(
        string='Date & Time',
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )
    amount = fields.Float(
        string='Amount (EGP)',
        required=True,
        digits=(10, 2),
    )
    transaction_type = fields.Selection(
        selection=[
            ('credit', 'Credit (Recharge)'),
            ('purchase', 'Purchase'),
            ('refund', 'Refund'),
            ('adjustment', 'Manual Adjustment'),
        ],
        string='Type',
        required=True,
        index=True,
    )
    reference = fields.Char(string='Reference', index=True)
    cashier_id = fields.Many2one(
        comodel_name='res.users',
        string='Processed By',
        readonly=True,
    )
    pos_order_id = fields.Many2one(
        comodel_name='pos.order',
        string='POS Order',
        readonly=True,
    )
    plan_id = fields.Many2one(
        comodel_name='cafeteria.plan',
        string='Meal Plan',
        help='Set when this transaction activated a plan.',
    )
    state = fields.Selection(
        selection=[
            ('done', 'Confirmed'),
            ('cancelled', 'Cancelled'),
        ],
        default='done',
        readonly=True,
    )
    note = fields.Text(string='Note')

    # ------------------------------------------------------------------ #
    #  Prevent editing confirmed transactions                              #
    # ------------------------------------------------------------------ #

    def write(self, vals):
        for txn in self:
            if txn.state == 'done' and not self.env.su:
                raise UserError('Confirmed transactions cannot be modified. Create an adjustment instead.')
        return super().write(vals)

    def unlink(self):
        for txn in self:
            if txn.state == 'done' and not self.env.su:
                raise UserError('Confirmed transactions cannot be deleted.')
        return super().unlink()
