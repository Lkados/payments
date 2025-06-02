# Copyright (c) 2017, Frappe Technologies and contributors
# License: MIT. See LICENSE

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log, make_get_request
from frappe.model.document import Document
from frappe.utils import call_hook_method, cint, flt, get_url

from payments.utils import create_payment_gateway


class StripeSettings(Document):
	supported_currencies = [
		"AED",
		"ALL",
		"ANG",
		"ARS",
		"AUD",
		"AWG",
		"BBD",
		"BDT",
		"BIF",
		"BMD",
		"BND",
		"BOB",
		"BRL",
		"BSD",
		"BWP",
		"BZD",
		"CAD",
		"CHF",
		"CLP",
		"CNY",
		"COP",
		"CRC",
		"CVE",
		"CZK",
		"DJF",
		"DKK",
		"DOP",
		"DZD",
		"EGP",
		"ETB",
		"EUR",
		"FJD",
		"FKP",
		"GBP",
		"GIP",
		"GMD",
		"GNF",
		"GTQ",
		"GYD",
		"HKD",
		"HNL",
		"HRK",
		"HTG",
		"HUF",
		"IDR",
		"ILS",
		"INR",
		"ISK",
		"JMD",
		"JPY",
		"KES",
		"KHR",
		"KMF",
		"KRW",
		"KYD",
		"KZT",
		"LAK",
		"LBP",
		"LKR",
		"LRD",
		"MAD",
		"MDL",
		"MNT",
		"MOP",
		"MRO",
		"MUR",
		"MVR",
		"MWK",
		"MXN",
		"MYR",
		"NAD",
		"NGN",
		"NIO",
		"NOK",
		"NPR",
		"NZD",
		"PAB",
		"PEN",
		"PGK",
		"PHP",
		"PKR",
		"PLN",
		"PYG",
		"QAR",
		"RUB",
		"SAR",
		"SBD",
		"SCR",
		"SEK",
		"SGD",
		"SHP",
		"SLL",
		"SOS",
		"STD",
		"SVC",
		"SZL",
		"THB",
		"TOP",
		"TTD",
		"TWD",
		"TZS",
		"UAH",
		"UGX",
		"USD",
		"UYU",
		"UZS",
		"VND",
		"VUV",
		"WST",
		"XAF",
		"XOF",
		"XPF",
		"YER",
		"ZAR",
	]

	currency_wise_minimum_charge_amount = {
		"JPY": 50,
		"MXN": 10,
		"DKK": 2.50,
		"HKD": 4.00,
		"NOK": 3.00,
		"SEK": 3.00,
		"USD": 0.50,
		"AUD": 0.50,
		"BRL": 0.50,
		"CAD": 0.50,
		"CHF": 0.50,
		"EUR": 0.50,
		"GBP": 0.30,
		"NZD": 0.50,
		"SGD": 0.50,
	}

	def on_update(self):
		create_payment_gateway(
			"Stripe-" + self.gateway_name,
			settings="Stripe Settings",
			controller=self.gateway_name,
		)
		call_hook_method("payment_gateway_enabled", gateway="Stripe-" + self.gateway_name)
		if not self.flags.ignore_mandatory:
			self.validate_stripe_credentails()

	def validate_stripe_credentails(self):
		if self.publishable_key and self.secret_key:
			header = {
				"Authorization": "Bearer {}".format(
					self.get_password(fieldname="secret_key", raise_exception=False)
				)
			}
			try:
				make_get_request(url="https://api.stripe.com/v1/charges", headers=header)
			except Exception:
				frappe.throw(_("Seems Publishable Key or Secret Key is wrong !!!"))

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Stripe does not support transactions in currency '{0}'"
				).format(currency)
			)

	def validate_minimum_transaction_amount(self, currency, amount):
		if currency in self.currency_wise_minimum_charge_amount:
			if flt(amount) < self.currency_wise_minimum_charge_amount.get(currency, 0.0):
				frappe.throw(
					_("For currency {0}, the minimum transaction amount should be {1}").format(
						currency, self.currency_wise_minimum_charge_amount.get(currency, 0.0)
					)
				)

	def get_payment_url(self, **kwargs):
		return get_url(f"./stripe_checkout?{urlencode(kwargs)}")

	def create_request(self, data):
		import stripe

		self.data = frappe._dict(data)
		stripe.api_key = self.get_password(fieldname="secret_key", raise_exception=False)
		stripe.default_http_client = stripe.http_client.RequestsClient()

		try:
			self.integration_request = create_request_log(self.data, service_name="Stripe")
			return self.create_charge_on_stripe()

		except Exception:
			frappe.log_error(frappe.get_traceback())
			return {
				"redirect_to": frappe.redirect_to_message(
					_("Server Error"),
					_(
						"It seems that there is an issue with the server's stripe configuration. In case of failure, the amount will get refunded to your account."
					),
				),
				"status": 401,
			}

	def create_charge_on_stripe(self):
		import stripe

		try:
			charge = stripe.Charge.create(
				amount=cint(flt(self.data.amount) * 100),
				currency=self.data.currency,
				source=self.data.stripe_token_id,
				description=self.data.description,
				receipt_email=self.data.payer_email,
			)

			if charge.captured == True:
				self.integration_request.db_set("status", "Completed", update_modified=False)
				self.flags.status_changed_to = "Completed"

			else:
				frappe.log_error(charge.failure_message, "Stripe Payment not completed")

		except Exception:
			frappe.log_error(frappe.get_traceback())

		return self.finalize_request()

	def finalize_request(self):
		redirect_to = self.data.get("redirect_to") or None
		redirect_message = self.data.get("redirect_message") or None
		status = self.integration_request.status

		if self.flags.status_changed_to == "Completed":
			if self.data.reference_doctype and self.data.reference_docname:
				custom_redirect_to = None
				try:
					custom_redirect_to = frappe.get_doc(
						self.data.reference_doctype, self.data.reference_docname
					).run_method("on_payment_authorized", self.flags.status_changed_to)
				except Exception:
					frappe.log_error(frappe.get_traceback())

				if custom_redirect_to:
					redirect_to = custom_redirect_to

				redirect_url = "payment-success?doctype={}&docname={}".format(
					self.data.reference_doctype, self.data.reference_docname
				)

			if self.redirect_url:
				redirect_url = self.redirect_url
				redirect_to = None
		else:
			redirect_url = "payment-failed"

		if redirect_to and "?" in redirect_url:
			redirect_url += "&" + urlencode({"redirect_to": redirect_to})
		else:
			redirect_url += "?" + urlencode({"redirect_to": redirect_to})

		if redirect_message:
			redirect_url += "&" + urlencode({"redirect_message": redirect_message})

		return {"redirect_to": redirect_url, "status": status}


def get_gateway_controller(doctype, docname, payment_gateway=None):
	if not payment_gateway:
		reference_doc = frappe.get_doc(doctype, docname)
		payment_gateway = reference_doc.payment_gateway
	gateway_controller = frappe.db.get_value("Payment Gateway", payment_gateway, "gateway_controller")
	return gateway_controller

@frappe.whitelist(allow_guest=True, methods=["POST"])
def stripe_webhook_handler():
	"""Handler pour les webhooks Stripe"""
	try:
		import json

		payload = frappe.local.request.get_data()
		signature = frappe.get_request_header("Stripe-Signature")

		# Parser l'événement
		event = json.loads(payload.decode('utf-8'))
		event_type = event.get("type")

		# Log de l'événement reçu
		frappe.log_error(f"Stripe webhook received: {event_type} - {event.get('id')}", "Stripe Webhook Received")

		# Traiter les événements de paiement
		if event_type == "charge.succeeded":
			handle_charge_succeeded(event)
		elif event_type == "payment_intent.succeeded":
			handle_payment_intent_succeeded(event)

		return {"status": "success", "received": True, "event_type": event_type}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Stripe Webhook Error")
		return {"status": "error", "message": str(e)}


def handle_charge_succeeded(event):
	"""Traite les événements charge.succeeded"""
	try:
		charge_data = event.get("data", {}).get("object", {})
		charge_id = charge_data.get("id")

		# Chercher l'Integration Request correspondante par charge_id
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status
			FROM `tabIntegration Request`
			WHERE service_name = 'Stripe'
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 5
		""", (f"%{charge_id}%", f"%{charge_id}%"), as_dict=True)

		if integration_requests:
			for req in integration_requests:
				if req.status != "Completed":
					# Mettre à jour le statut
					frappe.db.sql("""
						UPDATE `tabIntegration Request`
						SET status = 'Completed', modified = NOW()
						WHERE name = %s
					""", req.name)

					frappe.log_error(f"Updated Integration Request {req.name} to Completed", "Stripe Payment Success")

					# Déclencher les hooks sur le document de référence
					if req.reference_doctype and req.reference_docname:
						try:
							ref_doc = frappe.get_doc(req.reference_doctype, req.reference_docname)
							ref_doc.run_method("on_payment_authorized", "Completed")
							frappe.db.commit()
							frappe.log_error(f"Payment authorized for {req.reference_doctype} {req.reference_docname}", "Payment Hook Success")
						except Exception:
							frappe.log_error(frappe.get_traceback(), "Payment Hook Error")
					break
		else:
			frappe.log_error(f"No Integration Request found for charge {charge_id}", "Stripe Webhook Warning")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Charge Succeeded Error")


def handle_payment_intent_succeeded(event):
	"""Traite les événements payment_intent.succeeded"""
	try:
		payment_intent_data = event.get("data", {}).get("object", {})
		payment_intent_id = payment_intent_data.get("id")

		# Logique similaire pour payment_intent
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status
			FROM `tabIntegration Request`
			WHERE service_name = 'Stripe'
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 5
		""", (f"%{payment_intent_id}%", f"%{payment_intent_id}%"), as_dict=True)

		if integration_requests:
			for req in integration_requests:
				if req.status != "Completed":
					frappe.db.sql("""
						UPDATE `tabIntegration Request`
						SET status = 'Completed', modified = NOW()
						WHERE name = %s
					""", req.name)

					frappe.log_error(f"Updated Integration Request {req.name} to Completed", "Stripe Payment Intent Success")
					break

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Payment Intent Succeeded Error")
