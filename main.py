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
# ROOT ROUTE
# -----------------------------------

@app.get("/")
async def root():

    return {
        "status":
        "Restaurant Inventory AI Backend Running"
    }

# -----------------------------------
# CLEAN JSON
# -----------------------------------

def clean_json(raw_text):

    return (
        raw_text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

# -----------------------------------
# MATCH PRODUCTS
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
                raw_name in product_name_lower
                or
                product_name_lower in raw_name
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

        # -----------------------------
        # UPDATE EXISTING
        # -----------------------------

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

        # -----------------------------
        # CREATE NEW
        # -----------------------------

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
                "lbs",

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
# INVENTORY SCAN
# -----------------------------------

@app.post("/scan")
async def scan_inventory(
    file: UploadFile
):

    try:

        print("SCAN STARTED")

        # -----------------------------
        # READ IMAGE
        # -----------------------------

        image_bytes = await file.read()

        base64_image = (
            base64.b64encode(
                image_bytes
            ).decode("utf-8")
        )

        # -----------------------------
        # OPENAI REQUEST
        # -----------------------------

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

        print("RAW AI RESULT:")
        print(raw_result)

        cleaned_result = clean_json(
            raw_result
        )

        ai_products = json.loads(
            cleaned_result
        )

        print("AI PRODUCTS:")
        print(ai_products)

        # -----------------------------
        # CREATE SCAN RECORD
        # -----------------------------

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

        # -----------------------------
        # PROCESS PRODUCTS
        # -----------------------------

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

            # -------------------------
            # SAVE INVENTORY ITEM
            # -------------------------

            insert_response = (

                supabase.table(
                    "inventory_items"
                )

                .insert({

                    "inventory_scan_id":
                    inventory_scan_id,

                    "canonical_product_id":
                    canonical_product_id,

                    "estimated_quantity":
                    estimated_quantity,

                    "confidence":
                    confidence

                })

                .execute()
            )

            print(
                "ITEM INSERTED:"
            )

            print(insert_response.data)

            # -------------------------
            # UPDATE LIVE INVENTORY
            # -------------------------

            if matched_existing:

                update_live_inventory(
                    canonical_product_id,
                    estimated_quantity
                )

            # -------------------------
            # FRONTEND RESPONSE OBJECT
            # -------------------------

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
                canonical_product_id
            })

        # -----------------------------
        # FINAL RESPONSE
        # -----------------------------

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
            ).decode("utf-8")
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