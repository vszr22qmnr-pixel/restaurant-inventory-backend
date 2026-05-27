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
# JSON CLEANER
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
# SEMANTIC PRODUCT MATCHING
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

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "system",

                    "content": """
                    You are an expert restaurant inventory AI.

                    Match invoice/vendor/scanned products
                    to existing canonical products.

                    Consider:
                    - OCR errors
                    - abbreviations
                    - shorthand
                    - vendor naming
                    - singular/plural
                    - pack size naming

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
                            Identify all inventory items.

                            Return ONLY JSON.

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

                            Categories:
                            {CATEGORY_OPTIONS}

                            Base Units:
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

            canonical_product = None

            # ======================================
            # EXACT MATCH
            # ======================================

            alias_response = supabase.table(
                "product_aliases"
            ).select(
                "*"
            ).ilike(
                "raw_product_name",
                product_name
            ).execute()

            if alias_response.data:

                alias = alias_response.data[0]

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
                        canonical_response.data[0]
                    )

            # ======================================
            # SEMANTIC MATCH
            # ======================================

            if not canonical_product:

                semantic_match = (
                    semantic_match_product(
                        product_name
                    )
                )

                if semantic_match:

                    canonical_product = semantic_match

                    existing_alias = supabase.table(
                        "product_aliases"
                    ).select(
                        "*"
                    ).ilike(
                        "raw_product_name",
                        product_name
                    ).execute()

                    if not existing_alias.data:

                        supabase.table(
                            "product_aliases"
                        ).insert({

                            "raw_product_name":
                            product_name,

                            "canonical_product_id":
                            semantic_match["id"]

                        }).execute()

            # ======================================
            # UPDATE LIVE INVENTORY
            # ======================================

            if canonical_product:

                inventory_response = supabase.table(
                    "live_inventory"
                ).select(
                    "*"
                ).eq(
                    "canonical_product_id",
                    canonical_product["id"]
                ).execute()

                if inventory_response.data:

                    current_quantity = (
                        inventory_response.data[0][
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

            # ======================================
            # STORE SCAN HISTORY
            # ======================================

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

                "matched_existing":
                canonical_product is not None,

                "needs_creation":
                canonical_product is None,

                "suggested_category":
                suggested_category,

                "suggested_base_unit":
                suggested_base_unit
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

        supabase.table(
            "product_aliases"
        ).insert({

            "raw_product_name":
            product_name,

            "canonical_product_id":
            canonical_id

        }).execute()

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