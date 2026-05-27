from fastapi import FastAPI, UploadFile
from supabase import create_client
from openai import OpenAI

import base64
import json
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
# JSON CLEANER
# ==========================================

def clean_json(content):

    if not content:
        return ""

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
# AI CATEGORY + UNIT SUGGESTION
# ==========================================

def get_ai_product_metadata(
    product_name,
    fallback_unit="each"
):

    try:

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "system",

                    "content": """
                    You are a restaurant inventory AI.

                    Determine:
                    - best category
                    - best base unit

                    Return ONLY JSON.
                    """
                },

                {
                    "role": "user",

                    "content": f"""
                    Product:
                    {product_name}

                    Categories:
                    {CATEGORY_OPTIONS}

                    Base Units:
                    {BASE_UNIT_OPTIONS}

                    Return:

                    {{
                      "category": "",
                      "base_unit": ""
                    }}
                    """
                }
            ]
        )

        raw = (
            response
            .choices[0]
            .message
            .content
        )

        cleaned = clean_json(
            raw
        )

        data = json.loads(
            cleaned
        )

        return {

            "category":
            data.get(
                "category",
                "Other"
            ),

            "base_unit":
            data.get(
                "base_unit",
                fallback_unit
            )
        }

    except Exception as e:

        print(
            "AI METADATA ERROR"
        )

        print(e)

        return {

            "category":
            "Other",

            "base_unit":
            fallback_unit
        }

# ==========================================
# SEMANTIC PRODUCT MATCHING
# ==========================================

def semantic_match_product(
    scanned_name
):

    try:

        canonical_products = supabase.table(
            "canonical_products"
        ).select(
            "*"
        ).execute()

        products = canonical_products.data

        if not products:
            return None

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "system",

                    "content": """
                    You are an expert restaurant inventory AI.

                    Match:
                    - vendor shorthand
                    - invoice abbreviations
                    - OCR mistakes
                    - alternate naming

                    Return ONLY JSON.
                    """
                },

                {
                    "role": "user",

                    "content": f"""
                    Product:
                    {scanned_name}

                    Existing Products:
                    {json.dumps(products)}

                    Return:

                    {{
                      "matched": true,
                      "canonical_product_id": "",
                      "confidence": 0.0
                    }}

                    OR

                    {{
                      "matched": false
                    }}
                    """
                }
            ]
        )

        raw = (
            response
            .choices[0]
            .message
            .content
        )

        cleaned = clean_json(
            raw
        )

        result = json.loads(
            cleaned
        )

        if result.get(
            "matched"
        ):

            confidence = result.get(
                "confidence",
                0
            )

            if confidence >= 0.80:

                matched_id = result.get(
                    "canonical_product_id"
                )

                matched_product = next(

                    (
                        p for p in products

                        if p["id"] == matched_id
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
# CREATE NEW PRODUCT
# ==========================================

def create_new_product(
    product_name,
    quantity,
    unit
):

    ai_data = get_ai_product_metadata(
        product_name,
        unit
    )

    category = ai_data[
        "category"
    ]

    base_unit = ai_data[
        "base_unit"
    ]

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

    # ======================================
    # CREATE PRODUCT ALIAS
    # ======================================

    supabase.table(
        "product_aliases"
    ).insert({

        "raw_product_name":
        product_name,

        "canonical_product_id":
        canonical_id

    }).execute()

    # ======================================
    # INSERT INTO LIVE INVENTORY
    # ======================================

    supabase.table(
        "live_inventory"
    ).insert({

        "canonical_product_id":
        canonical_id,

        "current_quantity":
        quantity,

        "unit":
        base_unit,

        "par_level":
        0,

        "reorder_threshold":
        0

    }).execute()

    return canonical_product

# ==========================================
# INVENTORY IMAGE SCAN
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

                            "text": """
                            Identify all inventory items.

                            Return ONLY JSON.

                            [
                              {
                                "product_name": "",
                                "estimated_quantity": 0,
                                "confidence": 0.0,
                                "suggested_category": "",
                                "suggested_base_unit": ""
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

        raw = (
            response
            .choices[0]
            .message
            .content
        )

        cleaned = clean_json(
            raw
        )

        items = json.loads(
            cleaned
        )

        return {

            "success": True,

            "result":
            items
        }

    except Exception as e:

        print(
            "SCAN ERROR"
        )

        print(str(e))

        return {

            "success": False,

            "error": str(e)
        }

# ==========================================
# INVOICE SCANNER
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
                            Analyze this restaurant invoice.

                            Extract:
                            - product name
                            - quantity
                            - unit
                            - price

                            Return ONLY JSON.
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

        raw = (
            response
            .choices[0]
            .message
            .content
        )

        cleaned = clean_json(
            raw
        )

        invoice_items = json.loads(
            cleaned
        )

        processed_items = []

        for item in invoice_items:

            product_name = item.get(
                "product_name",
                ""
            )

            quantity = item.get(
                "quantity",
                1
            )

            unit = item.get(
                "unit",
                "each"
            )

            price = item.get(
                "price",
                0
            )

            # ==================================
            # SEMANTIC MATCH
            # ==================================

            canonical_product = (
                semantic_match_product(
                    product_name
                )
            )

            canonical_product_id = None

            # ==================================
            # EXISTING PRODUCT
            # ==================================

            if canonical_product:

                canonical_product_id = (
                    canonical_product["id"]
                )

            # ==================================
            # CREATE NEW PRODUCT
            # ==================================

            else:

                created_product = (
                    create_new_product(
                        product_name,
                        quantity,
                        unit
                    )
                )

                canonical_product_id = (
                    created_product["id"]
                )

            # ==================================
            # UPDATE LIVE INVENTORY
            # ==================================

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
                    inventory_response.data[0][
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

            else:

                supabase.table(
                    "live_inventory"
                ).insert({

                    "canonical_product_id":
                    canonical_product_id,

                    "current_quantity":
                    quantity,

                    "unit":
                    unit,

                    "par_level":
                    0,

                    "reorder_threshold":
                    0

                }).execute()

            # ==================================
            # STORE PROCESSED ITEM
            # ==================================

            processed_items.append({

                "product_name":
                product_name,

                "quantity":
                quantity,

                "unit":
                unit,

                "price":
                price,

                "canonical_product_id":
                canonical_product_id
            })

        return {

            "success": True,

            "invoice_items":
            processed_items
        }

    except Exception as e:

        print(
            "INVOICE ERROR:"
        )

        print(str(e))

        return {

            "success": False,

            "error": str(e)
        }