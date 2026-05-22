/** @odoo-module **/
console.log("CAFETERIA MODULE LOADED");

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useEffect } from "@odoo/owl";

var _cafeteriaCardUid = null;

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.orm = useService("orm");
        this.pos = usePos();

        useEffect(() => {
            this._injectScanUI();
        });
    },

    _injectScanUI() {
        if (document.getElementById("caf-wrap")) return;

        var subpads = document.querySelector(".product-screen .subpads") ||
                      document.querySelector(".subpads") ||
                      document.querySelector(".leftpane") ||
                      document.querySelector(".product-screen") ||
                      document.body;
        if (!subpads) {
            subpads = document.querySelector(".pos-app") ||
                      document.querySelector(".o_pos_kanban") ||
                      document.body;
        }

        var wrap = document.createElement("div");
        wrap.id = "caf-wrap";
        wrap.style.cssText = "margin:0 0 6px 0;padding:0";

        wrap.innerHTML =
            '<div id="caf-btn-row" style="display:flex;gap:4px">' +
                '<button id="caf-btn" style="flex:1;padding:6px 12px;font-size:13px;font-weight:600;border:none;border-radius:4px;cursor:pointer;background:#1D6B4A;color:#fff">' +
                    '<i class="fa fa-barcode"></i> Scan Card' +
                '</button>' +
            '</div>' +
            '<div id="caf-search-row" style="display:none;gap:4px">' +
                '<input id="caf-input" type="text" placeholder="Student code..." ' +
                    'style="flex:1;padding:6px 10px;font-size:13px;border:1px solid #ced4da;border-radius:4px;outline:none"/>' +
                '<button id="caf-cancel" ' +
                    'style="padding:6px 10px;font-size:13px;border:1px solid #ced4da;border-radius:4px;cursor:pointer;background:#fff;color:#666">&#x2715;</button>' +
            '</div>' +
            '<div id="caf-card" style="display:none;margin-top:4px;padding:8px;border:1px solid #e9ecef;border-radius:6px;background:#f8f9fa"></div>';

        subpads.insertBefore(wrap, subpads.firstChild);

        var self = this;
        document.getElementById("caf-btn").addEventListener("click", function () {
            document.getElementById("caf-btn-row").style.display = "none";
            document.getElementById("caf-search-row").style.display = "flex";
            setTimeout(function () { document.getElementById("caf-input").focus(); }, 50);
        });
        document.getElementById("caf-cancel").addEventListener("click", function () {
            self._resetScanUI();
        });
        document.getElementById("caf-input").addEventListener("keydown", function (ev) {
            if (ev.key === "Enter") {
                var val = ev.target.value.trim();
                if (val) {
                    document.getElementById("caf-input").disabled = true;
                    self._lookupStudent(val);
                }
            }
            if (ev.key === "Escape") self._resetScanUI();
        });
        console.log("CAFETERIA injected into", subpads.className);
    },

    _resetScanUI() {
        document.getElementById("caf-btn-row").style.display = "flex";
        document.getElementById("caf-search-row").style.display = "none";
        document.getElementById("caf-card").style.display = "none";
        document.getElementById("caf-input").value = "";
        document.getElementById("caf-input").disabled = false;
    },

    _showCard(student) {
        var card = document.getElementById("caf-card");
        if (!card) return;
        var html = '<div style="display:flex;align-items:center;gap:8px">';
        if (student.photo) {
            html += '<img src="data:image/png;base64,' + student.photo + '" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:1px solid #dee2e6"/>';
        } else {
            html += '<div style="width:36px;height:36px;border-radius:50%;background:#e9ecef;display:flex;align-items:center;justify-content:center;font-size:16px">&#x1F464;</div>';
        }
        html += '<div style="flex:1;min-width:0;line-height:1.3">' +
            '<div style="font-weight:600;font-size:13px;color:#212529">' + (student.name || "") + '</div>';
        if (student.grade || student.section) {
            html += '<div style="font-size:10px;color:#6c757d">';
            if (student.grade) html += student.grade;
            if (student.grade && student.section) html += ' &middot; ';
            if (student.section) html += student.section;
            html += '</div>';
        }
        html += '<div style="font-size:14px;font-weight:700;color:#1D6B4A">' +
            (student.balance || 0).toFixed(2) + ' EGP</div></div>';
        html += '<div style="font-size:10px;color:#6c757d;text-align:right">' +
            '<div>Today</div><div style="font-weight:600;color:#1D6B4A">' +
            (student.today_spent || 0).toFixed(2) + '</div>';
        if (student.daily_limit) {
            html += '<div style="font-size:9px;color:#adb5bd">/ ' + student.daily_limit.toFixed(2) + '</div>';
        }
        html += '</div></div>';
        card.innerHTML = html;
        card.style.display = "";
    },

    async _lookupStudent(cardUid) {
        if (!cardUid) return;
        try {
            var student = await this.orm.call("school.student", "get_student_by_uid", [cardUid]);
            if (!student) {
                alert("Card not found: " + cardUid);
                document.getElementById("caf-input").disabled = false;
                document.getElementById("caf-input").focus();
                return;
            }
            var order = this.pos.getOrder();
            if (!order) {
                alert("No active order — add a product first");
                document.getElementById("caf-input").disabled = false;
                return;
            }
            order._cafeteria_student = student;
            _cafeteriaCardUid = student.card_uid || student.student_code;
            this._showCard(student);
            document.getElementById("caf-btn").textContent = '\u2713 ' + student.name.split(" ")[0];
            if (student.parent_id) {
                var partner = this.pos.models["res.partner"].getBy("id", student.parent_id);
                if (!partner) {
                    var partners = await this.pos.data.searchRead("res.partner", [["id", "=", student.parent_id]]);
                    if (partners && partners.length) partner = partners[0];
                }
                if (partner) order.setPartner(partner);
            }
        } catch (e) {
            alert("Lookup failed: " + (e.message || e));
        }
    },
});

var _origSerialize = PosOrder.prototype.serializeForORM;
PosOrder.prototype.serializeForORM = function (opts) {
    var data = _origSerialize.call(this, opts);
    var student = this._cafeteria_student;
    var uid = student ? (student.card_uid || student.student_code) : (_cafeteriaCardUid || false);
    data.student_card_uid = uid;
    return data;
};
