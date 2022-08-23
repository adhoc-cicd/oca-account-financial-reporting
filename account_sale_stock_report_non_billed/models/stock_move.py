# Copyright 2022 Tecnativa - Carlos Roca
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    quantity_not_invoiced = fields.Float(
        string="Qty. to invoice",
        compute="_compute_not_invoiced_values",
        digits="Product Unit of Measure",
    )
    price_not_invoiced = fields.Float(
        string="Amount to invoice",
        compute="_compute_not_invoiced_values",
        digits="Product Price",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency", compute="_compute_currency_id"
    )
    date_done = fields.Date(
        string="Effective Date", compute="_compute_date_done", store=True
    )

    @api.depends("picking_id.date_done")
    def _compute_date_done(self):
        self.date_done = False
        for move in self:
            if move.picking_id.date_done:
                move.date_done = move.picking_id.date_done.date()

    @api.depends("sale_line_id")
    def _compute_currency_id(self):
        """Method to be overwritten when new implementations are made, e.g. with
        purchase_stock_picking_invoice_link.
        """
        self.currency_id = False
        for move in self:
            if move.sale_line_id:
                move.currency_id = move.sale_line_id.currency_id

    def check_is_return(self):
        self.ensure_one()
        if not self.origin_returned_move_id:
            return False
        else:
            return not self.origin_returned_move_id.check_is_return()

    def get_total_devolution_moves(self):
        total_qty = 0
        for move in self:
            if not move.check_is_return():
                total_qty += move.quantity_done
            else:
                total_qty -= move.quantity_done
        return total_qty

    def get_quantity_invoiced(self, invoice_lines):
        if not invoice_lines:
            return 0
        total_invoiced = abs(
            sum(
                invoice_lines.mapped(
                    lambda l: l.quantity
                    if (l.move_id.type == "out_invoice" and not self.to_refund)
                    or (l.move_id.type == "out_refund" and self.to_refund)
                    else -l.quantity
                )
            )
        )
        # Check when grouping different moves in an invoice line
        moves = invoice_lines.mapped("move_line_ids")
        total_qty = moves.get_total_devolution_moves()
        if total_invoiced != total_qty:
            invoiced = 0.0
            for move in moves:
                qty = (
                    move.quantity_done
                    if move.quantity_done <= (total_invoiced - invoiced)
                    else total_invoiced - invoiced
                )
                if move.check_is_return():
                    qty = -qty
                if move == self:
                    return qty
                invoiced += qty
            return 0
        return self.quantity_done if not self.check_is_return() else -self.quantity_done

    def _set_not_invoiced_values(self, qty_to_invoice, invoiced_qty):
        self.ensure_one()
        self.quantity_not_invoiced = qty_to_invoice - invoiced_qty
        self.price_not_invoiced = (
            qty_to_invoice - invoiced_qty
        ) * self.sale_line_id.price_unit

    @api.depends("sale_line_id")
    @api.depends_context("date_check_invoiced_moves")
    def _compute_not_invoiced_values(self):
        for move in self:
            if not self.env.context.get("date_check_invoiced_moves"):
                move.quantity_not_invoiced = 0
                move.price_not_invoiced = 0
                continue
            inv_lines = (
                move.invoice_line_ids.filtered(lambda l: l.move_id.state != "cancel")
                .mapped("move_line_ids.invoice_line_ids")
                .filtered(
                    lambda l: l.check_invoice_line_in_date(
                        fields.Date.from_string(
                            self.env.context["date_check_invoiced_moves"]
                        )
                    )
                )
            )
            qty_to_invoice = (
                move.quantity_done
                if not move.check_is_return()
                else -move.quantity_done
            )
            calculated_qty = move.get_quantity_invoiced(inv_lines)
            move._set_not_invoiced_values(qty_to_invoice, calculated_qty)

    @api.model
    def read_group(
        self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True
    ):
        """Method to add the computed values 'quantity_not_invoiced' and
        'price_not_invoiced' to the result. Without doing it we get an error when trying
        to get the info on a pivot view.
        As the fields are not stored, before call super() method we had to remove
        the keys from 'fields' argument to avoid errors.
        """
        aux_fields = []
        if "quantity_not_invoiced:sum" in fields:
            aux_fields.append("quantity_not_invoiced:sum")
            fields.remove("quantity_not_invoiced:sum")
        if "price_not_invoiced:sum" in fields:
            aux_fields.append("price_not_invoiced:sum")
            fields.remove("price_not_invoiced:sum")
        res = super().read_group(
            domain,
            fields,
            groupby,
            offset=offset,
            limit=limit,
            orderby=orderby,
            lazy=lazy,
        )
        qty_not_inv = "quantity_not_invoiced:sum" in aux_fields
        price_not_inv = "price_not_invoiced:sum" in aux_fields
        if qty_not_inv or price_not_inv:
            for line in res:
                quantity = 0.0
                price = 0.0
                moves = self.search(line.get("__domain", domain))
                for move in moves:
                    quantity += move.quantity_not_invoiced if qty_not_inv else 0.0
                    price += move.price_not_invoiced if price_not_inv else 0.0
                line["quantity_not_invoiced"] = quantity
                line["price_not_invoiced"] = price
        return res

    def _get_model_id_origin_document(self):
        if not self.sale_line_id:
            return
        return self.sale_line_id.order_id._name, self.sale_line_id.order_id.id

    def open_origin_document(self):
        model, res_id = self._get_model_id_origin_document()
        return {
            "type": "ir.actions.act_window",
            "views": [(False, "form")],
            "view_mode": "form",
            "res_model": model,
            "res_id": res_id,
            "context": self.env.context,
        }
