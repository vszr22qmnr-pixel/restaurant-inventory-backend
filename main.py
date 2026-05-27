from fastapi import FastAPI, UploadFile
from supabase import create_client
from openai import OpenAI

import base64
import json
import uuid
import os

# ==========================================
# ENV VARIABLES
# ==========================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)

# ==========================================
# CLIENTS
# ==========================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)

client = OpenAI(
    api_key=OPENAI_API_KEY
)

# ==========================================
# FASTAPI
# ==========================================

app = FastAPI()

# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/")
async def root():

    return {
        "status":
        "Restaurant Inventory AI Backend Running"
    }

# ==========================================
# CATEGORY OPTIONS
# ==========================================

CATEGORY_OPTIONS = [

    "Produce",

    "Dairy",

    "Meat",

    "Seafood",

    "Frozen",

    "Dry Storage",

    "Bakery",

    "Beverages",

    "Beer",

    "Wine",

    "Liquor",

    "Non-Alcoholic Drinks",

    "Paper Goods",

    "Cleaning",

    "Other",
]

# ==========================================
# BASE UNIT OPTIONS
# ==========================================

BASE_UNIT_OPTIONS = [

    "lbs",

    "each",

    "case",

    "oz",

    "gallon",

    "bag",

    "box",

    "bottle",

    "can",
]

# ==========================================
# HELPER
# ==========================================

def clean_json(content):
    content = content.replace(
        "```json",
        ""
    )

    content = content.replace(
        "```",
        ""
    )

    return content.strip()

    # ==========================================
# AI SEMANTIC PRODUCT MATCHING
# ==========================================

def semantic_match_product(
    scanned_name
):

    try:

        canonical_products = supabase.table(
            "canonical_products"
        ).select(
            "id, canonical_name, category, base_unit"
        ).execute()

        products = canonical_products.data

        if not products:
            return None

        product_list = []

        for product in products:

            product_list.append({

                "id":
                product["id"],

                "canonical_name":
                product["canonical_name"],

                "category":
                product.get(
                    "category",
                    "Other"
                ),

                "base_unit":
                product.get(
                    "base_unit",
                    "each"
                )
            })

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "system",

                    "content": """
                    You are an expert restaurant inventory AI.

                    Your job is to determine if a scanned product
                    semantically matches an existing canonical product.

                    Consider:
                    - abbreviations
                    - OCR mistakes
                    - vendor shorthand
                    - pack sizes
                    - naming variations
                    - plural/singular
                    - brand variations

                    Return ONLY valid JSON.
                    """
                },

                {
                    "role": "user",

                    "content": f"""
                    Scanned Product:

                    {scanned_name}

                    Existing Canonical Products:

                    {json.dumps(product_list)}

                    Return format:

                    {{
                      "matched": true,
                      "canonical_product_id": "",
                      "confidence": 0.0
                    }}

                    OR

                    {{
                      "matched": false
                    }}

                    ONLY match if confidence is high.
                    """
                }
            ]
        )

        content = response.choices[
            0
        ].message.content

        cleaned = clean_json(
            content
        )

        result = json.loads(
            cleaned
        )

        if result.get(
            "matched"
        ) == True:

            matched_id = result.get(
                "canonical_product_id"
            )

            confidence = result.get(
                "confidence",
                0.0
            )

            if confidence >= 0.80:

                matched_product = next(

                    (
                        p for p in products

                        if p["id"] ==
                        matched_id
                    ),

                    None
                )

                return matched_product

        return None

    except Exception as e:

        print(
            "SEMANTIC MATCH ERROR"
        )

        print(e)

        return None

    
# ==========================================
# INVENTORY SCAN
# ==========================================

@app.post("/scan")
async def scan_inventory(
    file: UploadFile
):

    try:

        image_bytes = await file.read()

        base64_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "user",

                    "content": [

                        {
                            "type": "text",

                            "text": f"""
                            Identify all visible restaurant inventory items.

                            Return ONLY valid JSON.

                            Format:

                            [
                              {{
                                "product_name": "",
                                "estimated_quantity": 0,
                                "confidence": 0.0,
                                "suggested_category": "",
                                "suggested_base_unit": ""
                              }}
                            ]

                            Possible categories:

                            {CATEGORY_OPTIONS}

                            Possible base units:

                            {BASE_UNIT_OPTIONS}
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

        content = response.choices[
            0
        ].message.content

        cleaned = clean_json(
            content
        )

        items = json.loads(
            cleaned
        )

        final_results = []

        for item in items:

            product_name = item.get(
                "product_name",
                ""
            ).strip()

            estimated_quantity = item.get(
                "estimated_quantity",
                1
            )

            confidence = item.get(
                "confidence",
                0.0
            )

            suggested_category = item.get(
                "suggested_category",
                "Other"
            )

            suggested_base_unit = item.get(
                "suggested_base_unit",
                "each"
            )

            # =====================================
            # CHECK ALIASES
            # =====================================

            alias_response = supabase.table(
                "product_aliases"
            ).select(
                "*"
            ).ilike(
                "alias_name",
                product_name
            ).execute()

            matched_existing = False

            canonical_product = None

            if alias_response.data:

                matched_existing = True

                alias = alias_response.data[
                    0
                ]

                canonical_id = alias[
                    "canonical_product_id"
                ]

                canonical_response = supabase.table(
                    "canonical_products"
                ).select(
                    "*"
                ).eq(
                    "id",
                    canonical_id
                ).execute()

                if canonical_response.data:

                    canonical_product = (
                        canonical_response.data[
                            0
                        ]
                    )

            # =====================================
            # CREATE INVENTORY ITEM
            # =====================================

            if matched_existing and canonical_product:

                existing_inventory = supabase.table(
                    "live_inventory"
                ).select(
                    "*"
                ).eq(
                    "canonical_product_id",
                    canonical_product["id"]
                ).execute()

                if existing_inventory.data:

                    current_quantity = (
                        existing_inventory.data[
                            0
                        ][
                            "current_quantity"
                        ]
                    )

                    updated_quantity = (
                        current_quantity
                        + estimated_quantity
                    )

                    supabase.table(
                        "live_inventory"
                    ).update({

                        "current_quantity":
                        updated_quantity

                    }).eq(

                        "canonical_product_id",
                        canonical_product["id"]

                    ).execute()

                else:

                    supabase.table(
                        "live_inventory"
                    ).insert({

                        "canonical_product_id":
                        canonical_product["id"],

                        "current_quantity":
                        estimated_quantity,

                        "unit":
                        canonical_product.get(
                            "base_unit",
                            suggested_base_unit
                        ),

                        "par_level":
                        0,

                        "reorder_threshold":
                        0

                    }).execute()

            # =====================================
            # STORE SCAN HISTORY
            # =====================================

            supabase.table(
                "inventory_scans"
            ).insert({

                "product_name":
                product_name,

                "estimated_quantity":
                estimated_quantity,

                "confidence":
                confidence

            }).execute()

            final_results.append({

                "product_name":
                product_name,

                "estimated_quantity":
                estimated_quantity,

                "confidence":
                confidence,

                "suggested_category":
                suggested_category,

                "suggested_base_unit":
                suggested_base_unit,

                "matched_existing":
                matched_existing,

                "needs_creation":
                not matched_existing
            })

        return {

            "success": True,

            "result":
            final_results
        }

    except Exception as e:

        print(e)

        return {

            "success": False,

            "error": str(e)
        }

# ==========================================
# CREATE PRODUCT
# ==========================================

@app.post("/create_product")
async def create_product(
    payload: dict
):

    try:

        product_name = payload.get(
            "product_name"
        )

        estimated_quantity = payload.get(
            "estimated_quantity",
            1
        )

        category = payload.get(
            "category",
            "Other"
        )

        base_unit = payload.get(
            "base_unit",
            "each"
        )

        # =====================================
        # CREATE CANONICAL PRODUCT
        # =====================================

        canonical_response = supabase.table(
            "canonical_products"
        ).insert({

            "canonical_name":
            product_name,

            "category":
            category,

            "base_unit":
            base_unit

        }).execute()

        canonical_product = (
            canonical_response.data[0]
        )

        canonical_id = canonical_product[
            "id"
        ]

        # =====================================
        # CREATE ALIAS
        # =====================================

        supabase.table(
            "product_aliases"
        ).insert({

            "alias_name":
            product_name,

            "canonical_product_id":
            canonical_id

        }).execute()

        # =====================================
        # CREATE LIVE INVENTORY
        # =====================================

        supabase.table(
            "live_inventory"
        ).insert({

            "canonical_product_id":
            canonical_id,

            "current_quantity":
            estimated_quantity,

            "unit":
            base_unit,

            "par_level":
            0,

            "reorder_threshold":
            0

        }).execute()

        return {

            "success": True,

            "canonical_product":
            canonical_product
        }

    except Exception as e:

        print(e)

        return {

            "success": False,

            "error": str(e)
        }

# ==========================================
# INVOICE SCAN
# ==========================================

@app.post("/scan_invoice")
async def scan_invoice(
    file: UploadFile
):

    try:

        image_bytes = await file.read()

        base64_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "user",

                    "content": [

                        {
                            "type": "text",

                            "text": """
                            This is a restaurant invoice.

                            Extract ALL visible invoice items.

                            Return ONLY valid JSON.

                            Format:

                            [
                              {
                                "product_name": "",
                                "quantity": 0,
                                "unit_price": 0,
                                "total_price": 0
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

        content = response.choices[
            0
        ].message.content

        cleaned = clean_json(
            content
        )

        invoice_items = json.loads(
            cleaned
        )

        final_results = []

        # =====================================
        # CREATE INVOICE
        # =====================================

        invoice_id = str(
            uuid.uuid4()
        )

        supabase.table(
            "invoices"
        ).insert({

            "id":
            invoice_id

        }).execute()

        # =====================================
        # PROCESS ITEMS
        # =====================================

        for item in invoice_items:

            product_name = item.get(
                "product_name",
                ""
            )

            quantity = item.get(
                "quantity",
                1
            )

            unit_price = item.get(
                "unit_price",
                0
            )

            total_price = item.get(
                "total_price",
                0
            )

            alias_response = supabase.table(
                "product_aliases"
            ).select(
                "*"
            ).ilike(
                "alias_name",
                product_name
            ).execute()

            canonical_product_id = None

            if alias_response.data:

                canonical_product_id = (
                    alias_response.data[0][
                        "canonical_product_id"
                    ]
                )

                inventory_response = supabase.table(
                    "live_inventory"
                ).select(
                    "*"
                ).eq(
                    "canonical_product_id",
                    canonical_product_id
                ).execute()

                if inventory_response.data:

                    current_quantity = (
                        inventory_response.data[
                            0
                        ][
                            "current_quantity"
                        ]
                    )

                    updated_quantity = (
                        current_quantity
                        + quantity
                    )

                    supabase.table(
                        "live_inventory"
                    ).update({

                        "current_quantity":
                        updated_quantity

                    }).eq(

                        "canonical_product_id",
                        canonical_product_id

                    ).execute()

            supabase.table(
                "invoice_items"
            ).insert({

                "invoice_id":
                invoice_id,

                "product_name":
                product_name,

                "quantity":
                quantity,

                "unit_price":
                unit_price,

                "total_price":
                total_price,

                "canonical_product_id":
                canonical_product_id

            }).execute()

            final_results.append({

                "product_name":
                product_name,

                "quantity":
                quantity,

                "unit_price":
                unit_price,

                "total_price":
                total_price
            })

        return {

            "success": True,

            "result":
            final_results
        }

    except Exception as e:

        print(e)

        return {

            "success": False,

            "error": str(e)
        }