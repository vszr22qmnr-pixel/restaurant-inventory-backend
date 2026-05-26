from fastapi import FastAPI, UploadFile
from openai import OpenAI
from supabase import create_client

import base64
import json
import os

# -----------------------------------
# ENVIRONMENT VARIABLES
# -----------------------------------

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)

# -----------------------------------
# SUPABASE CLIENT
# -----------------------------------

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)

# -----------------------------------
# OPENAI CLIENT
# -----------------------------------

client = OpenAI(
    api_key=OPENAI_API_KEY
)

# -----------------------------------
# FASTAPI APP
# -----------------------------------

app = FastAPI()

# -----------------------------------
# RESTAURANT ID
# -----------------------------------

RESTAURANT_ID = (
    "486f4c9d-dcdd-4d8e-9f40-97c733182a5e"
)

# -----------------------------------
# CLEAN OPENAI JSON
# -----------------------------------

def clean_json(raw_text):

    return (

        raw_text

        .replace(
            "```json",
            ""
        )

        .replace(
            "```",
            ""
        )

        .strip()
    )

# -----------------------------------
# AI PRODUCT ENRICHMENT
# -----------------------------------

def enrich_product_data(
    product_name
):

    try:

        enrichment_response = (

            client.chat.completions.create(

                model="gpt-4.1-mini",

                messages=[

                    {
                        "role": "user",

                        "content":
                        f"""

                        Given this restaurant inventory item:

                        {product_name}

                        Determine:

                        1. Best inventory category
                        2. Best base unit

                        Return ONLY valid JSON.

                        Example:

                        {{
                          "category": "Produce",
                          "base_unit": "lbs"
                        }}
                        """
                    }
                ]
            )
        )

        enrichment_raw = (

            enrichment_response
            .choices[0]
            .message
            .content
        )

        enrichment_cleaned = (
            clean_json(
                enrichment_raw
            )
        )

        enrichment_data = json.loads(
            enrichment_cleaned
        )

        return {

            "category":
            enrichment_data.get(
                "category",
                "Other"
            ),

            "base_unit":
            enrichment_data.get(
                "base_unit",
                "each"
            )
        }

    except Exception as e:

        print(
            "ENRICHMENT ERROR:"
        )

        print(str(e))

        return {

            "category":
            "Other",

            "base_unit":
            "each"
        }

# -----------------------------------
# MATCH PRODUCT
# -----------------------------------

def match_product(product_name):

    try:

        response = (

            supabase.table(
                "product_aliases"
            )

            .select("*")

            .execute()
        )

        aliases = response.data

        product_name_lower = (
            product_name.lower().strip()
        )

        for alias in aliases:

            raw_name = (

                alias[
                    "raw_product_name"
                ]

                .lower()

                .strip()
            )

            if (

                raw_name
                in
                product_name_lower

                or

                product_name_lower
                in
                raw_name
            ):

                return alias[
                    "canonical_product_id"
                ]

        return None

    except Exception as e:

        print(
            "MATCH ERROR:"
        )

        print(str(e))

        return None

# -----------------------------------
# CREATE NEW PRODUCT
# -----------------------------------

def create_new_product(
    product_name,
    estimated_quantity,
    category,
    base_unit
):

    try:

        canonical_insert = (

            supabase.table(
                "canonical_products"
            )

            .insert({

                "canonical_name":
                product_name,

                "category":
                category,

                "base_unit":
                base_unit,

                "created_by_ai":
                True,

                "ai_confidence":
                0.9

            })

            .execute()
        )

        canonical_product_id = (

            canonical_insert
            .data[0]["id"]
        )

        # -----------------------------
        # CREATE ALIAS
        # -----------------------------

        supabase.table(
            "product_aliases"
        ).insert({

            "raw_product_name":
            product_name,

            "canonical_product_id":
            canonical_product_id

        }).execute()

        # -----------------------------
        # CREATE LIVE INVENTORY
        # -----------------------------

        supabase.table(
            "live_inventory"
        ).insert({

            "restaurant_id":
            RESTAURANT_ID,

            "canonical_product_id":
            canonical_product_id,

            "current_quantity":
            estimated_quantity,

            "unit":
            base_unit,

            "par_level":
            20,

            "reorder_threshold":
            5

        }).execute()

        return canonical_product_id

    except Exception as e:

        print(
            "CREATE PRODUCT ERROR:"
        )

        print(str(e))

        return None

# -----------------------------------
# UPDATE LIVE INVENTORY
# -----------------------------------

def update_live_inventory(
    canonical_product_id,
    estimated_quantity
):

    try:

        existing_inventory = (

            supabase.table(
                "live_inventory"
            )

            .select("*")

            .eq(
                "canonical_product_id",
                canonical_product_id
            )

            .execute()
        )

        if existing_inventory.data:

            existing_item = (
                existing_inventory
                .data[0]
            )

            current_quantity = float(

                existing_item[
                    "current_quantity"
                ]
            )

            new_quantity = (
                current_quantity
                +
                estimated_quantity
            )

            supabase.table(
                "live_inventory"
            ).update({

                "current_quantity":
                new_quantity

            }).eq(

                "id",
                existing_item["id"]

            ).execute()

        else:

            supabase.table(
                "live_inventory"
            ).insert({

                "restaurant_id":
                RESTAURANT_ID,

                "canonical_product_id":
                canonical_product_id,

                "current_quantity":
                estimated_quantity,

                "unit":
                "each",

                "par_level":
                20,

                "reorder_threshold":
                5

            }).execute()

    except Exception as e:

        print(
            "LIVE INVENTORY ERROR:"
        )

        print(str(e))

# -----------------------------------
# ROOT ROUTE
# -----------------------------------

@app.get("/")
async def root():

    return {

        "status":
        "Restaurant Inventory AI Backend Running"
    }

# -----------------------------------
# SCAN INVENTORY
# -----------------------------------

@app.post("/scan")
async def scan_inventory(
    file: UploadFile
):

    try:

        print("SCAN STARTED")

        image_bytes = await file.read()

        base64_image = (

            base64.b64encode(
                image_bytes
            )

            .decode("utf-8")
        )

        response = (

            client.chat.completions.create(

                model="gpt-4.1-mini",

                messages=[

                    {
                        "role": "user",

                        "content": [

                            {
                                "type": "text",

                                "text": """
                                Identify all visible restaurant inventory items.

                                Return ONLY valid JSON.

                                Format:

                                [
                                  {
                                    "product_name": "",
                                    "estimated_quantity": 0,
                                    "confidence": 0.0
                                  }
                                ]
                                """
                            },

                            {
                                "type": "image_url",

                                "image_url": {

                                    "url":
                                    f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )
        )

        raw_result = (

            response
            .choices[0]
            .message
            .content
        )

        cleaned_result = clean_json(
            raw_result
        )

        ai_products = json.loads(
            cleaned_result
        )

        scan_insert = (

            supabase.table(
                "inventory_scans"
            )

            .insert({

                "restaurant_id":
                RESTAURANT_ID,

                "image_url":
                "temporary"

            })

            .execute()
        )

        inventory_scan_id = (
            scan_insert
            .data[0]["id"]
        )

        processed_products = []

        for item in ai_products:

            product_name = item.get(
                "product_name",
                ""
            )

            estimated_quantity = float(
                item.get(
                    "estimated_quantity",
                    0
                )
            )

            confidence = float(
                item.get(
                    "confidence",
                    0
                )
            )

            canonical_product_id = (
                match_product(
                    product_name
                )
            )

            matched_existing = (
                canonical_product_id
                is not None
            )

            enrichment = (
                enrich_product_data(
                    product_name
                )
            )

            suggested_category = (
                enrichment["category"]
            )

            suggested_base_unit = (
                enrichment["base_unit"]
            )

            supabase.table(
                "inventory_items"
            ).insert({

                "inventory_scan_id":
                inventory_scan_id,

                "canonical_product_id":
                canonical_product_id,

                "estimated_quantity":
                estimated_quantity,

                "confidence":
                confidence

            }).execute()

            if matched_existing:

                update_live_inventory(
                    canonical_product_id,
                    estimated_quantity
                )

            processed_products.append({

                "product_name":
                product_name,

                "estimated_quantity":
                estimated_quantity,

                "confidence":
                confidence,

                "matched_existing":
                matched_existing,

                "canonical_product_id":
                canonical_product_id,

                "needs_creation":
                canonical_product_id is None,

                "suggested_category":
                suggested_category,

                "suggested_base_unit":
                suggested_base_unit
            })

        return {

            "success": True,

            "inventory_scan_id":
            inventory_scan_id,

            "result":
            processed_products
        }

    except Exception as e:

        print("SCAN ERROR:")
        print(str(e))

        return {

            "success": False,

            "error": str(e)
        }

# -----------------------------------
# CREATE PRODUCT
# -----------------------------------

@app.post("/create_product")
async def create_product(
    product_data: dict
):

    try:

        product_name = (
            product_data[
                "product_name"
            ]
        )

        estimated_quantity = float(

            product_data.get(
                "estimated_quantity",
                0
            )
        )

        category = (
            product_data.get(
                "category",
                "Other"
            )
        )

        base_unit = (
            product_data.get(
                "base_unit",
                "each"
            )
        )

        canonical_product_id = (
            create_new_product(

                product_name,

                estimated_quantity,

                category,

                base_unit
            )
        )

        return {

            "success": True,

            "canonical_product_id":
            canonical_product_id
        }

    except Exception as e:

        print(
            "CREATE PRODUCT ENDPOINT ERROR:"
        )

        print(str(e))

        return {

            "success": False,

            "error": str(e)
        }

# -----------------------------------
# INVOICE SCANNER
# -----------------------------------

@app.post("/scan_invoice")
async def scan_invoice(
    file: UploadFile
):

    try:

        image_bytes = await file.read()

        base64_image = (

            base64.b64encode(
                image_bytes
            )

            .decode("utf-8")
        )

        response = (

            client.chat.completions.create(

                model="gpt-4.1-mini",

                messages=[

                    {
                        "role": "user",

                        "content": [

                            {
                                "type": "text",

                                "text": """
                                Extract invoice data.

                                Return ONLY valid JSON.
                                """
                            },

                            {
                                "type": "image_url",

                                "image_url": {

                                    "url":
                                    f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )
        )

        raw_result = (

            response
            .choices[0]
            .message
            .content
        )

        cleaned_result = clean_json(
            raw_result
        )

        invoice_data = json.loads(
            cleaned_result
        )

        return {

            "success": True,

            "result":
            invoice_data
        }

    except Exception as e:

        print("INVOICE ERROR:")
        print(str(e))

        return {

            "success": False,

            "error": str(e)
        }