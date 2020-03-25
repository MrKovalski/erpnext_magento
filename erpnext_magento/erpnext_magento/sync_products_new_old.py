from __future__ import unicode_literals
import frappe
from frappe import _
import requests.exceptions
from erpnext_magento.erpnext_magento.exceptions import MagentoError
from erpnext_magento.erpnext_magento.utils import make_magento_log, disable_magento_sync_for_item
from erpnext.stock.utils import get_bin
from frappe.utils import cstr, flt, cint, get_files_path
from erpnext_magento.erpnext_magento.magento_requests import get_magento_items, get_magento_item_atrribute_values, post_request, put_request
import base64, requests, datetime, os

magento_variants_attr_list = ["option1", "option2", "option3"]

def sync_products():
	magento_item_list = []
	sync_magento_items(magento_item_list)
	frappe.local.form_dict.count_dict["erpnext_products"] = len(magento_item_list)

	erpnext_item_list = []
	# sync_erpnext_items(erpnext_item_list)¨
	frappe.local.form_dict.count_dict["magento_products"] = len(erpnext_item_list)

def sync_magento_items(magento_item_list):
	for magento_item in get_magento_items():
		try:
			make_item(magento_item, magento_item_list)

		except MagentoError as e:
			make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
				request_data=magento_item, exception=True)

		except Exception as e:
			if e.args[0] and e.args[0].startswith("402"):
				raise e
			else:
				make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
					request_data=magento_item, exception=True)

def make_item(magento_item, magento_item_list):
	magento_settings = frappe.get_doc("Magento Settings", "Magento Settings")

	if has_variants(magento_item):
		attributes = create_attribute(magento_item)
		create_item(magento_item, magento_settings, 1, attributes, magento_item_list=magento_item_list)
		#create_item_variants(magento_item, magento_settings.warehouse, attributes, magento_variants_attr_list, magento_item_list=magento_item_list)

	#else:
	#	magento_item["variant_id"] = magento_item['variants'][0]["id"]
	#	create_item(magento_item, warehouse, magento_item_list=magento_item_list)

def has_variants(magento_item):
	if magento_item.get("type_id") == "configurable":
		return True
	return False

def create_attribute(magento_item):
	attribute_list = []
	# magento item dict
	for magento_attr in magento_item.get("extension_attributes").get("configurable_product_options"):
		if not frappe.db.get_value("Item Attribute", magento_attr.get("label"), "name"):
			attr = frappe.get_doc({
				"doctype": "Item Attribute",
				"attribute_name": magento_attr.get("label")
			})

			attr_dict.flags.ignore_mandatory = True
			attr_dict.insert()

			for magento_attribute_value in get_magento_item_atrribute_values(magento_attr.get("attribute_id")):
				if not magento_attribute_value.get("label") == " ":
					attr_value = frappe.get_doc({
						"doctype": "Item Attribute Value",
						"attribute_value": magento_attribute_value.get("label"),
						"abbr": magento_attribute_value.get("label").upper(),
						"parentfield": "item_attribute_values",
						"parenttype": "Item Attribute",
						"parent": magento_attr.get("label"),
						"idx":99
					})

					attr_value.flags.ignore_mandatory = True
					attr_value.insert()

			attribute_list.append({"attribute": magento_attr.get("label")})

		else:
			# check for attribute values
			item_attr = frappe.get_doc("Item Attribute", magento_attr.get("label"))
			if not item_attr.numeric_values:
				for magento_attribute_value in get_magento_item_atrribute_values(magento_attr.get("attribute_id")):
					if not magento_attribute_value.get("label") == " " and not is_attribute_value_exists(item_attr.item_attribute_values, magento_attribute_value):
						attr_value = frappe.get_doc({
							"doctype": "Item Attribute Value",
							"attribute_value": magento_attribute_value.get("label"),
							"abbr": magento_attribute_value.get("label").upper(),
							"parentfield": "item_attribute_values",
							"parenttype": "Item Attribute",
							"parent": item_attr.name,
							"idx":99
						})

						attr_value.flags.ignore_mandatory = True
						attr_value.insert()
		
				attribute_list.append({"attribute": magento_attr.get("label")})

			else:
				attribute.append({
					"attribute": magento_attr.get("label"),
					"from_range": item_attr.get("from_range"),
					"to_range": item_attr.get("to_range"),
					"increment": item_attr.get("increment"),
					"numeric_values": item_attr.get("numeric_values")
				})

	return attribute_list

def is_attribute_value_exists(attribute_values, magento_attribute_value):
	for value in attribute_values:
		if value.get("attribute_value") == magento_attribute_value.get("label"):
			return True
	
	return False

def create_item(magento_item, magento_settings, has_variant=0, attributes=None,variant_of=None, magento_item_list=[]):
	item_dict = {
		"doctype": "Item",
		"magento_product_id": magento_item.get("id"),
		"magento_variant_id": magento_item.get("variant_id"),
		"variant_of": variant_of,
		"sync_with_magento": 1,
		"is_stock_item": 0,
		"item_code": magento_item.get("name"),
		"item_name": magento_item.get("name"),
		"item_group": magento_settings.item_group,
		"has_variants": has_variant,
		"attributes": attributes or [],
		"stock_uom": magento_item.get("uom") or _("Nos"),
		"stock_keeping_unit": magento_item.get("sku") or get_sku(magento_item),
		"default_warehouse": magento_settings.warehouse,
		"default_supplier": get_supplier(magento_item),
		"default_material_request_type": "Manufacture"
	}

	if not is_item_exists(item_dict, attributes, variant_of=variant_of, magento_item_list=magento_item_list):
		item_details = get_item_details(magento_item)

		if not item_details:
			new_item = frappe.get_doc(item_dict)
			new_item.insert()
			name = new_item.name

		else:
			update_item(item_details, item_dict)
			name = item_details.name

		if not has_variant:
			add_to_price_list(magento_item, name)

		frappe.db.commit()

def create_item_variants(magento_item, warehouse, attributes, magento_variants_attr_list, magento_item_list):
	template_item = frappe.db.get_value("Item", filters={"magento_product_id": magento_item.get("id")},
		fieldname=["name", "stock_uom"], as_dict=True)

	if template_item:
		for variant in magento_item.get("variants"):
			magento_item_variant = {
				"id" : variant.get("id"),
				"item_code": magento_item.get("title") + ": " + variant.get("title"),
				"title": magento_item.get("title") + ": " + variant.get("title"),
				"product_type": magento_item.get("product_type"),
				"sku": variant.get("sku"),
				"uom": template_item.stock_uom or _("Nos"),
				"item_price": variant.get("price"),
				"variant_id": variant.get("id"),
				"weight_unit": variant.get("weight_unit"),
				"weight": variant.get("weight"),
				"variant_image": get_variant_image(magento_item, variant)
			}

			for i, variant_attr in enumerate(magento_variants_attr_list):
				if variant.get(variant_attr):
					attributes[i].update({"attribute_value": get_attribute_value(variant.get(variant_attr), attributes[i])})
			create_item(magento_item_variant, warehouse, 0, attributes, template_item.name, magento_item_list=magento_item_list)

def get_attribute_value(variant_attr_val, attribute):
	attribute_value = frappe.db.sql("""select attribute_value from `tabItem Attribute Value`
		where parent = %s and (abbr = %s or attribute_value = %s)""", (attribute["attribute"], variant_attr_val,
		variant_attr_val), as_list=1)
	return attribute_value[0][0] if len(attribute_value)>0 else cint(variant_attr_val)

def get_sku(item):
	if item.get("variants"):
		return item.get("variants")[0].get("sku")
	return ""

def add_to_price_list(item, name):
	item_price_name = frappe.db.get_value("Item Price", {"item_code": name}, "name")
	if not item_price_name:
		frappe.get_doc({
			"doctype": "Item Price",
			"price_list": frappe.get_doc("Magento Settings", "Magento Settings").price_list,
			"item_code": name,
			"price_list_rate": item.get("item_price") or item.get("variants")[0].get("price")
		}).insert()
	else:
		item_rate = frappe.get_doc("Item Price", item_price_name)
		item_rate.price_list_rate = item.get("item_price") or item.get("variants")[0].get("price")
		item_rate.save()

def get_item_image(magento_item):
    if magento_item.get("image"):
        return magento_item.get("image").get("src")
    elif magento_item.get("variant_image"):
        return magento_item.get("variant_image")
    return None

def get_variant_image(magento_item, variant):
    if variant.get("image_id"):
        for image in magento_item.get("images"):
            if image.get("id") == variant.get("image_id"):
                return image.get("src")
    return None

def get_supplier(magento_item):
	if magento_item.get("vendor"):
		supplier = frappe.db.sql("""select name from tabSupplier
			where name = %s or magento_supplier_id = %s """, (magento_item.get("vendor"),
			magento_item.get("vendor").lower()), as_list=1)

		if not supplier:
			supplier = frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": magento_item.get("vendor"),
				"magento_supplier_id": magento_item.get("vendor").lower(),
				"supplier_type": get_supplier_type()
			}).insert()
			return supplier.name
		else:
			return magento_item.get("vendor")
	else:
		return ""

def get_supplier_type():
	supplier_type = frappe.db.get_value("Supplier Type", _("Magento Supplier"))
	if not supplier_type:
		supplier_type = frappe.get_doc({
			"doctype": "Supplier Type",
			"supplier_type": _("Magento Supplier")
		}).insert()
		return supplier_type.name
	return supplier_type

def get_item_details(magento_item):
	item_details = {}

	item_details = frappe.db.get_value("Item", {"magento_product_id": magento_item.get("id")},
		["name", "item_name"], as_dict=1)

	if item_details:
		return item_details

	else:
		item_details = frappe.db.get_value("Item", {"magento_variant_id": magento_item.get("id")},
			["name", "item_name"], as_dict=1)
		return item_details

def is_item_exists(magento_item, attributes=None, variant_of=None, magento_item_list=[]):
	if variant_of:
		name = variant_of
	else:
		name = frappe.db.get_value("Item", {"item_name": magento_item.get("item_name")})

	magento_item_list.append(cstr(magento_item.get("magento_product_id")))

	if name:
		item = frappe.get_doc("Item", name)
		item.flags.ignore_mandatory=True

		if not variant_of and not item.magento_product_id:
			item.magento_product_id = magento_item.get("magento_product_id")
			item.magento_variant_id = magento_item.get("magento_variant_id")
			item.save()
			return False

		if item.magento_product_id and attributes and attributes[0].get("attribute_value"):
			if not variant_of:
				variant_of = frappe.db.get_value("Item",
					{"magento_product_id": item.magento_product_id}, "variant_of")

			# create conditions for all item attributes,
			# as we are putting condition basis on OR it will fetch all items matching either of conditions
			# thus comparing matching conditions with len(attributes)
			# which will give exact matching variant item.

			conditions = ["(iv.attribute='{0}' and iv.attribute_value = '{1}')"\
				.format(attr.get("attribute"), attr.get("attribute_value")) for attr in attributes]

			conditions = "( {0} ) and iv.parent = it.name ) = {1}".format(" or ".join(conditions), len(attributes))

			parent = frappe.db.sql(""" select * from tabItem it where
				( select count(*) from `tabItem Variant Attribute` iv
					where {conditions} and it.variant_of = %s """.format(conditions=conditions) ,
				variant_of, as_list=1)

			if parent:
				variant = frappe.get_doc("Item", parent[0][0])
				variant.flags.ignore_mandatory = True

				variant.magento_product_id = magento_item.get("magento_product_id")
				variant.magento_variant_id = magento_item.get("magento_variant_id")
				variant.save()
			return False

		if item.magento_product_id and item.magento_product_id != magento_item.get("magento_product_id"):
			return False

		return True

	else:
		return False

def update_item(item_details, item_dict):
	item = frappe.get_doc("Item", item_details.name)
	item_dict["stock_uom"] = item_details.stock_uom

	del item_dict["item_code"]
	del item_dict["variant_of"]
	del item_dict["item_name"]

	item.update(item_dict)
	item.flags.ignore_mandatory = True
	item.save()

def sync_erpnext_items(magento_item_list):
	for item in get_erpnext_items():
		if item.magento_product_id not in magento_item_list:
			try:
				sync_item_with_magento(item, price_list, warehouse)
				frappe.local.form_dict.count_dict["products"] += 1

			except MagentoError as e:
				make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
					request_data=item, exception=True)
			except Exception as e:
				make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
					request_data=item, exception=True)

def get_erpnext_items():
	erpnext_items = []
	magento_settings = frappe.get_doc("Magento Settings", "Magento Settings")

	last_sync_condition, item_price_condition = "", ""
	if magento_settings.last_sync_datetime:
		last_sync_condition = "and modified >= '{0}' ".format(magento_settings.last_sync_datetime)
		item_price_condition = "and ip.modified >= '{0}' ".format(magento_settings.last_sync_datetime)

	item_from_master = """select name, item_code, item_name, item_group,
		description, magento_description, has_variants, variant_of, stock_uom, image, magento_product_id, 
		magento_variant_id, sync_qty_with_magento, net_weight, weight_uom, default_supplier from tabItem
		where sync_with_magento=1 and (variant_of is null or variant_of = '')
		and (disabled is null or disabled = 0)  %s """ % last_sync_condition

	erpnext_items.extend(frappe.db.sql(item_from_master, as_dict=1))

	template_items = [item.name for item in erpnext_items if item.has_variants]

	if len(template_items) > 0:
		item_price_condition += ' and i.variant_of not in (%s)'%(' ,'.join(["'%s'"]*len(template_items)))%tuple(template_items)

	item_from_item_price = """select i.name, i.item_code, i.item_name, i.item_group, i.description,
		i.magento_description, i.has_variants, i.variant_of, i.stock_uom, i.image, i.magento_product_id,
		i.magento_variant_id, i.sync_qty_with_magento, i.net_weight, i.weight_uom,
		i.default_supplier from `tabItem` i, `tabItem Price` ip
		where price_list = '%s' and i.name = ip.item_code
			and sync_with_magento=1 and (disabled is null or disabled = 0) %s""" %(price_list, item_price_condition)

	updated_price_item_list = frappe.db.sql(item_from_item_price, as_dict=1)

	# to avoid item duplication
	return [frappe._dict(tupleized) for tupleized in set(tuple(item.items())
		for item in erpnext_items + updated_price_item_list)]

def sync_item_with_magento(item, price_list, warehouse):
	variant_item_name_list = []

	item_data = { "product":
		{
			"title": item.get("item_name"),
			"body_html": item.get("magento_description") or item.get("description"),
			"product_type": item.get("item_group"),
			"vendor": item.get("default_supplier"),
			"published_scope": "global",
			"published_status": "published",
			"published_at": datetime.datetime.now().isoformat()
		}
	}

	if item.get("has_variants") or item.get("variant_of"):

		if item.get("variant_of"):
			item = frappe.get_doc("Item", item.get("variant_of"))

		variant_list, options, variant_item_name = get_variant_attributes(item, price_list, warehouse)

		item_data["product"]["title"] = item.get("item_name")
		item_data["product"]["body_html"] = item.get("magento_description") or item.get("description")
		item_data["product"]["variants"] = variant_list
		item_data["product"]["options"] = options

		variant_item_name_list.extend(variant_item_name)

	else:
		item_data["product"]["variants"] = [get_price_and_stock_details(item, warehouse, price_list)]

	erp_item = frappe.get_doc("Item", item.get("name"))
	erp_item.flags.ignore_mandatory = True
	
	if not item.get("magento_product_id"):
		create_new_item_to_magento(item, item_data, erp_item, variant_item_name_list)

	else:
		item_data["product"]["id"] = item.get("magento_product_id")
		try:
			put_request("/admin/products/{}.json".format(item.get("magento_product_id")), item_data)

		except requests.exceptions.HTTPError as e:
			if e.args[0] and e.args[0].startswith("404"):
				if frappe.db.get_value("Magento Settings", "Magento Settings", "if_not_exists_create_item_to_magento"):
					item_data["product"]["id"] = ''
					create_new_item_to_magento(item, item_data, erp_item, variant_item_name_list)
				else:
					disable_magento_sync_for_item(erp_item)
			else:
				raise e

	sync_item_image(erp_item)
	frappe.db.commit()

def create_new_item_to_magento(item, item_data, erp_item, variant_item_name_list):
	new_item = post_request("/admin/products.json", item_data)
	erp_item.magento_product_id = new_item['product'].get("id")

	if not item.get("has_variants"):
		erp_item.magento_variant_id = new_item['product']["variants"][0].get("id")

	erp_item.save()
	update_variant_item(new_item, variant_item_name_list)

def sync_item_image(item):
	image_info = {
        "image": {}
	}

	if item.image:
		img_details = frappe.db.get_value("File", {"file_url": item.image}, ["file_name", "content_hash"])

		if img_details and img_details[0] and img_details[1]:
			is_private = item.image.startswith("/private/files/")

			with open(get_files_path(img_details[0].strip("/"), is_private=is_private), "rb") as image_file:
				image_info["image"]["attachment"] = base64.b64encode(image_file.read())
			image_info["image"]["filename"] = img_details[0]

			#to avoid 422 : Unprocessable Entity
			if not image_info["image"]["attachment"] or not image_info["image"]["filename"]:
				return False

		elif item.image.startswith("http") or item.image.startswith("ftp"):
			if validate_image_url(item.image):
				#to avoid 422 : Unprocessable Entity
				image_info["image"]["src"] = item.image

		if image_info["image"]:
			if not item_image_exists(item.magento_product_id, image_info):
				# to avoid image duplication
				post_request("/admin/products/{0}/images.json".format(item.magento_product_id), image_info)

def validate_image_url(url):
	""" check on given url image exists or not"""
	res = requests.get(url)
	if res.headers.get("content-type") in ('image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/tiff'):
		return True
	return False

def item_image_exists(magento_product_id, image_info):
	"""check same image exist or not"""
	for image in get_magento_item_image(magento_product_id):
		if image_info.get("image").get("filename"):
			if os.path.splitext(image.get("src"))[0].split("/")[-1] == os.path.splitext(image_info.get("image").get("filename"))[0]:
				return True
		elif image_info.get("image").get("src"):
			if os.path.splitext(image.get("src"))[0].split("/")[-1] == os.path.splitext(image_info.get("image").get("src"))[0].split("/")[-1]:
				return True
		else:
			return False

def update_variant_item(new_item, item_code_list):
	for i, name in enumerate(item_code_list):
		erp_item = frappe.get_doc("Item", name)
		erp_item.flags.ignore_mandatory = True
		erp_item.magento_product_id = new_item['product']["variants"][i].get("id")
		erp_item.magento_variant_id = new_item['product']["variants"][i].get("id")
		erp_item.save()

def get_variant_attributes(item, price_list, warehouse):
	options, variant_list, variant_item_name, attr_sequence = [], [], [], []
	attr_dict = {}

	for i, variant in enumerate(frappe.get_all("Item", filters={"variant_of": item.get("name")},
		fields=['name'])):

		item_variant = frappe.get_doc("Item", variant.get("name"))
		variant_list.append(get_price_and_stock_details(item_variant, warehouse, price_list))

		for attr in item_variant.get('attributes'):
			if attr.attribute not in attr_sequence:
				attr_sequence.append(attr.attribute)

			if not attr_dict.get(attr.attribute):
				attr_dict.setdefault(attr.attribute, [])

			attr_dict[attr.attribute].append(attr.attribute_value)

			if attr.idx <= 3:
				variant_list[i]["option"+cstr(attr.idx)] = attr.attribute_value

		variant_item_name.append(item_variant.name)

	for i, attr in enumerate(attr_sequence):
		options.append({
			"name": attr,
			"position": i+1,
			"values": list(set(attr_dict[attr]))
		})

	return variant_list, options, variant_item_name

def get_price_and_stock_details(item, warehouse, price_list):
	qty = frappe.db.get_value("Bin", {"item_code":item.get("item_code"), "warehouse": warehouse}, "actual_qty")
	price = frappe.db.get_value("Item Price", \
			{"price_list": price_list, "item_code":item.get("item_code")}, "price_list_rate")

	item_price_and_quantity = {
		"price": flt(price)
	}

	if item.net_weight:
		if item.weight_uom and item.weight_uom.lower() in ["kg", "g", "oz", "lb"]:
			item_price_and_quantity.update({
				"weight_unit": item.weight_uom.lower(),
				"weight": item.net_weight,
				"grams": get_weight_in_grams(item.net_weight, item.weight_uom)
			})


	if item.get("sync_qty_with_magento"):
		item_price_and_quantity.update({
			"inventory_quantity": cint(qty) if qty else 0,
			"inventory_management": "magento"
		})

	if item.magento_variant_id:
		item_price_and_quantity["id"] = item.magento_variant_id

	return item_price_and_quantity

def get_weight_in_grams(weight, weight_uom):
	convert_to_gram = {
		"kg": 1000,
		"lb": 453.592,
		"oz": 28.3495,
		"g": 1
	}

	return weight * convert_to_gram[weight_uom.lower()]

def trigger_update_item_stock(doc, method):
	if doc.flags.via_stock_ledger_entry:
		magento_settings = frappe.get_doc("Magento Settings", "Magento Settings")
		if magento_settings.magento_url and magento_settings.enable_magento:
			update_item_stock(doc.item_code, magento_settings, doc)

def update_item_stock_qty():
	magento_settings = frappe.get_doc("Magento Settings", "Magento Settings")
	for item in frappe.get_all("Item", fields=['name', "item_code"],
		filters={"sync_with_magento": 1, "disabled": ("!=", 1), 'magento_variant_id': ('!=', '')}):
		try:
			update_item_stock(item.item_code, magento_settings)
		except MagentoError as e:
			make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
				request_data=item, exception=True)

		except Exception as e:
			if e.args[0] and e.args[0].startswith("402"):
				raise e
			else:
				make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
					request_data=item, exception=True)

def update_item_stock(item_code, magento_settings, bin=None):
	item = frappe.get_doc("Item", item_code)
	if item.sync_qty_with_magento:
		if not bin:
			bin = get_bin(item_code, magento_settings.warehouse)

		if not item.magento_product_id and not item.variant_of:
			sync_item_with_magento(item, magento_settings.price_list, magento_settings.warehouse)

		if item.sync_with_magento and item.magento_product_id and magento_settings.warehouse == bin.warehouse:
			if item.variant_of:
				item_data, resource = get_product_update_dict_and_resource(frappe.get_value("Item",
					item.variant_of, "magento_product_id"), item.magento_variant_id, is_variant=True,
					actual_qty=bin.actual_qty)
			else:
				item_data, resource = get_product_update_dict_and_resource(item.magento_product_id,
					item.magento_variant_id, actual_qty=bin.actual_qty)

			try:
				put_request(resource, item_data)
			except requests.exceptions.HTTPError as e:
				if e.args[0] and e.args[0].startswith("404"):
					make_magento_log(title=e.message, status="Error", method="sync_magento_items", message=frappe.get_traceback(),
						request_data=item_data, exception=True)
					disable_magento_sync_for_item(item)
				else:
					raise e

def get_product_update_dict_and_resource(magento_product_id, magento_variant_id, is_variant=False, actual_qty=0):
	"""
	JSON required to update product

	item_data =	{
		"product": {
			"id": 3649706435 (magento_product_id),
			"variants": [
				{
					"id": 10577917379 (magento_variant_id),
					"inventory_management": "magento",
					"inventory_quantity": 10
				}
			]
		}
	}
	"""

	item_data = {
		"product": {
			"variants": []
		}
	}

	varient_data = {
		"id": magento_variant_id,
		"inventory_quantity": cint(actual_qty),
		"inventory_management": "magento"
	}

	if is_variant:
		item_data = {
			"variant": varient_data
		}
		resource = "admin/variants/{}.json".format(magento_variant_id)
	else:
		item_data["product"]["id"] = magento_product_id
		item_data["product"]["variants"].append(varient_data)
		resource = "admin/products/{}.json".format(magento_product_id)

	return item_data, resource
