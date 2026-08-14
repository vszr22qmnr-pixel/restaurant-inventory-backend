from fastapi import FastAPI, UploadFile, Form
from supabase import create_client
from openai import OpenAI
from PIL import Image
import io

import base64
import json
import os
import fitz

# ==========================================

# ENV VARIABLES

# ==========================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ==========================================

# CLIENTS

# ==========================================

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================

# FASTAPI

# ==========================================

app = FastAPI()

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

# HEALTH CHECK

# ==========================================

@app.get("/")
async def root():
    return {"status": "Restaurant Inventory AI Backend Running"}

# ==========================================

# JSON CLEANER

# ==========================================

def clean_json(content):
    if not content:
        return ""

    content = content.replace("```json", "")
    content = content.replace("```", "")

    return content.strip()

# ==========================================

# AI CATEGORY + UNIT

# ==========================================

def get_ai_product_metadata(product_name, fallback_unit="each"):
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
                """,
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
                """,
                },
            ],
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)

        return json.loads(cleaned)

    except Exception as e:
        print(e)
        return {"category": "Other", "base_unit": fallback_unit}

# ==========================================

# SEMANTIC MATCHING

# ==========================================

def semantic_match_product(scanned_name):
    try:
        products_response = supabase.table("canonical_products").select("*").execute()
        products = products_response.data

        if not products:
            return None

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
                Match vendor shorthand,
                OCR mistakes,
                abbreviations,
                alternate names,
                and invoice names
                to canonical inventory products.

                Return ONLY JSON.
                """,
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
                """,
                },
            ],
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)
        result = json.loads(cleaned)

        if result.get("matched"):
            confidence = result.get("confidence", 0)
            if confidence >= 0.80:
                matched_id = result.get("canonical_product_id")
                for product in products:
                    if product["id"] == matched_id:
                        return product

        return None

    except Exception as e:
        print(e)
        return None

# ==========================================

# CREATE PRODUCT

# ==========================================

def create_new_product(
    product_name,
    quantity,
    unit,
    organization_id,
    location_id,
):
    metadata = get_ai_product_metadata(product_name, unit)
    category = metadata.get("category", "Other")
    base_unit = metadata.get("base_unit", unit)

    canonical_response = supabase.table("canonical_products").insert(
        {
            "canonical_name": product_name,
            "category": category,
            "base_unit": base_unit,
        }
    ).execute()

    canonical_product = canonical_response.data[0]
    canonical_id = canonical_product["id"]

    supabase.table("product_aliases").insert(
        {
            "raw_product_name": product_name,
            "canonical_product_id": canonical_id,
        }
    ).execute()

    supabase.table("live_inventory").insert(
    {
        "canonical_product_id": canonical_id,
        "current_quantity": quantity,
        "unit": base_unit,
        "estimated_unit_cost": 0,
        "par_level": 0,
        "reorder_threshold": 0,
        "organization_id": organization_id,
        "location_id": location_id,
    }
).execute()

    return canonical_product

# ==========================================

# SAVE PURCHASE HISTORY

# ==========================================

def save_purchase_history(
    canonical_product_id,
    vendor_name,
    invoice_date,
    item_name,
    quantity,
    unit,
    unit_cost,
    organization_id,
    location_id,
):
    try:
        quantity = float(quantity)
        unit_cost = float(unit_cost)

        total_cost = quantity * unit_cost

        print("SAVING PURCHASE")
        print("Organization:", organization_id)
        print("Location:", location_id)
        print("Total Cost:", total_cost)

        supabase.table("purchases").insert(
            {
                "canonical_product_id": canonical_product_id,
                "vendor_name": vendor_name,
                "invoice_date": invoice_date,
                "item_name": item_name,
                "quantity": quantity,
                "unit": unit,
                "unit_cost": unit_cost,
                "total_cost": total_cost,

                # RESTAURANT / LOCATION OWNERSHIP
                "organization_id": organization_id,
                "location_id": location_id,
            }
        ).execute()

    except Exception as e:
        print("PURCHASE SAVE ERROR")
        print(e)

# ==========================================

# PROCESS INVENTORY ITEMS

# ==========================================

def process_inventory_items(
    items,
    organization_id,
    location_id,
):
    processed_items = []

    for item in items:
        product_name = item.get("product_name", "")
        quantity = item.get("estimated_quantity", 1)
        unit = item.get("suggested_base_unit", "each")

        canonical_product = semantic_match_product(product_name)

        if canonical_product:
            canonical_id = canonical_product["id"]
        else:
            created_product = create_new_product(
    product_name,
    quantity,
    unit,
    organization_id,
    location_id,
)
            canonical_id = created_product["id"]

        inventory_response = (
    supabase.table("live_inventory")
    .select("*")
    .eq("canonical_product_id", canonical_id)
    .eq("organization_id", organization_id)
    .execute()
)

        if inventory_response.data:
            current_quantity = inventory_response.data[0]["current_quantity"]
            updated_quantity = current_quantity + quantity

            supabase.table("live_inventory").update(
    {
        "current_quantity": updated_quantity,
    }
).eq(
    "canonical_product_id",
    canonical_id,
).eq(
    "organization_id",
    organization_id,
).execute()
            
        else:
         supabase.table("live_inventory").insert(
        {
            "canonical_product_id": canonical_id,
            "current_quantity": quantity,
            "unit": unit,
            "estimated_unit_cost": 0,
            "par_level": 0,
            "reorder_threshold": 0,
            "organization_id": organization_id,
            "location_id": location_id,
        }
    ).execute()

        processed_items.append(
            {
                "product_name": product_name,
                "quantity": quantity,
                "canonical_product_id": canonical_id,
            }
        )

    return processed_items

# ==========================================

# IMAGE INVENTORY SCAN

# ==========================================

@app.post("/scan")
async def scan_inventory(
    file: UploadFile,
    organization_id: str = Form(...),
    location_id: str = Form(...),
):
    try:
        image_bytes = await file.read()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

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
                            "estimated_quantity": 0
                          }
                        ]
                        """,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)
        items = json.loads(cleaned)
        processed = process_inventory_items(
    items,
    organization_id,
    location_id,
)

        return {"success": True, "items": processed}

    except Exception as e:
        print(e)
        return {"success": False, "error": str(e)}

# ==========================================

# INVOICE SCANNER

# ==========================================

@app.post("/scan_invoice")
async def scan_invoice(
    file: UploadFile,
    organization_id: str = Form(...),
    location_id: str = Form(...),
):
    try:
        file_bytes = await file.read()

        if file.filename.lower().endswith(".pdf"):
            pdf = fitz.open(
                stream=file_bytes,
                filetype="pdf",
            )

            if len(pdf) == 0:
                return {
                    "success": False,
                    "error": "PDF contains no pages",
                }

            page = pdf[0]

            pix = page.get_pixmap(
                matrix=fitz.Matrix(2, 2),
            )

            image_bytes = pix.tobytes("png")
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
        else:
            base64_image = base64.b64encode(file_bytes).decode("utf-8")

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """
Analyze this restaurant invoice carefully.

For EVERY invoice line item extract:

- product_name
- quantity
- unit
- unit_price
- line_total

IMPORTANT RULES:

1. quantity = the actual number of units shown on the invoice.
2. unit_price = the price for ONE unit.
3. line_total = the total dollar amount for that invoice line.
4. NEVER put the line total into unit_price.
5. If the invoice clearly shows quantity and line_total but the unit price is not shown, calculate:

   unit_price = line_total / quantity

6. Example:

   Quantity: 75
   Line Total: $25.50

   Then:
   unit_price = 0.34
   line_total = 25.50

7. Do NOT use invoice subtotal, tax, delivery charges, or grand total as a product line total.
8. Each product must be returned as its own line item.
9. Preserve decimal values exactly when possible.

Return ONLY JSON.

[
  {
    "product_name": "",
    "quantity": 0,
    "unit": "",
    "unit_price": 0,
    "line_total": 0
  }
]
""",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)
        invoice_items = json.loads(cleaned)

        processed_items = []

        for item in invoice_items:
            product_name = item.get("product_name", "")
            quantity = float(item.get("quantity", 1))
            unit = item.get("unit", "each")

            unit_price = float(item.get("unit_price", 0))
            line_total = float(item.get("line_total", 0))

            # Calculate the true unit cost from the invoice line total
            # whenever a valid quantity and line total are available.
            if quantity > 0 and line_total > 0:
             calculated_unit_price = line_total / quantity

            # Use the calculated value from the invoice math.
            price = calculated_unit_price
        else:
            price = unit_price

            # Final safety check
            if price < 0:
             price = 0

            canonical_product = semantic_match_product(product_name)

            if canonical_product:
                canonical_id = canonical_product["id"]
            else:
                created_product = create_new_product(
    product_name,
    quantity,
    unit,
    organization_id,
    location_id,
)
                canonical_id = created_product["id"]

            inventory_response = (
    supabase.table("live_inventory")
    .select("*")
    .eq("canonical_product_id", canonical_id)
    .eq("organization_id", organization_id)
    .execute()
)

            if inventory_response.data:
                current_quantity = inventory_response.data[0]["current_quantity"]
                updated_quantity = current_quantity + quantity

                supabase.table("live_inventory").update(
    {
        "current_quantity": updated_quantity,
        "estimated_unit_cost": price,
    }
).eq(
    "canonical_product_id",
    canonical_id,
).eq(
    "organization_id",
    organization_id,
).execute()
                
            else:
             supabase.table("live_inventory").insert(
        {
            "canonical_product_id": canonical_id,
            "current_quantity": quantity,
            "unit": unit,
            "estimated_unit_cost": price,
            "par_level": 0,
            "reorder_threshold": 0,
            "organization_id": organization_id,
            "location_id": location_id,
        }
    ).execute()

            save_purchase_history(
    canonical_id,
    "Unknown Vendor",
    "today",
    product_name,
    quantity,
    unit,
    price,
    organization_id,
    location_id,
)

            processed_items.append(
                {
                    "product_name": product_name,
                    "quantity": quantity,
                    "unit": unit,
                    "price": round(price, 4),
                    "line_total": round(price * quantity, 2),
                    "canonical_product_id": canonical_id,
                }
            )

        return {"success": True, "vendor_name": "Unknown Vendor", "items": processed_items}

    except Exception as e:
        print("INVOICE ERROR")
        print(e)
        return {"success": False, "error": str(e)}


# ==========================================
# RECIPE SCANNER
# ==========================================

@app.post("/scan_recipe")
async def scan_recipe(
    file: UploadFile,
    organization_id: str = Form(...),
    location_id: str = Form(...),
):
    try:
        file_bytes = await file.read()

        # If PDF, convert pages to images
        base64_images = []
        if file.filename.lower().endswith(".pdf"):
            pdf = fitz.open(stream=file_bytes, filetype="pdf")

            if len(pdf) == 0:
                return {"success": False, "error": "PDF contains no pages"}

            for page_number in range(len(pdf)):
                page = pdf[page_number]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                image_bytes = pix.tobytes("png")
                base64_images.append(base64.b64encode(image_bytes).decode("utf-8"))

            pdf.close()
        else:
            base64_images = [base64.b64encode(file_bytes).decode("utf-8")]

        # Use the first image for analysis
        base64_image = base64_images[0]

        response_content = [
            {
                "type": "text",
                "text": """
Analyze this restaurant invoice.

The invoice may contain multiple pages.

Read ALL provided pages before returning the result.

Extract EVERY invoice line item from EVERY page.

For each item extract:

- product name
- quantity
- unit
- unit_price
- line_total

IMPORTANT:

1. quantity = the actual quantity purchased.
2. unit_price = the price for ONE purchased unit.
3. line_total = the total price for that invoice line.
4. NEVER use the line total as the unit price.
5. If quantity and line_total are available but unit_price is not clearly printed, calculate:

   unit_price = line_total / quantity

6. Example:

   quantity = 75
   line_total = 25.50

   unit_price = 0.34

7. Do NOT include:
   - subtotal
   - sales tax
   - delivery charges
   - invoice total
   - payments
   - discounts unless they are clearly part of a product line

8. Include products from ALL pages.

9. Do not stop after the first page.

10. Do not duplicate an item simply because it appears near a page break.

Return ONLY valid JSON.

[
  {
    "product_name": "",
    "quantity": 0,
    "unit": "",
    "unit_price": 0,
    "line_total": 0
  }
]
""",
            }
        ]

        for base64_image in base64_images:
            response_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    },
                }
            )

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "user",
                    "content": response_content,
                }
            ],
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)

        recipe = json.loads(cleaned)

        ingredients = recipe.get("ingredients", [])

        total_cost = 0

        processed_ingredients = []

        for ingredient in ingredients:

            ingredient_name = ingredient.get(
                "ingredient", ""
            )

            quantity = float(
                ingredient.get(
                    "quantity", 0
                )
            )

            unit = ingredient.get(
                "unit", "each"
            )

            matched_product = semantic_match_product(
                ingredient_name
            )

            ingredient_cost = 0

            if matched_product:

                inventory = (
    supabase.table("live_inventory")
    .select("*")
    .eq(
        "canonical_product_id",
        matched_product["id"],
    )
    .eq(
        "organization_id",
        organization_id,
    )
    .execute()
)

                if inventory.data:

                    unit_cost = float(
                        inventory.data[0].get(
                            "estimated_unit_cost",
                            0,
                        )
                    )

                    ingredient_cost = (
                        quantity * unit_cost
                    )

                    total_cost += ingredient_cost

            processed_ingredients.append(
                {
                    "ingredient":
                        ingredient_name,
                    "quantity":
                        quantity,
                    "unit": unit,
                    "cost":
                        round(
                            ingredient_cost,
                            2,
                        ),
                }
            )

        servings = float(
            recipe.get("servings", 1)
        )

        cost_per_serving = (
            total_cost / servings
            if servings > 0
            else total_cost
        )

        # ==========================================
        # SAVE RECIPE
        # ==========================================

        recipe_response = (
            supabase.table("recipes")
            .insert(
                {
                    "organization_id": organization_id,
                    "recipe_name": recipe.get(
                        "recipe_name",
                        "Unknown Recipe",
                    ),
                    "recipe_json": recipe,
                    "total_cost": round(total_cost, 2),
                    "cost_per_serving": round(cost_per_serving, 2),
                    "servings": servings,
                }
            )
            .execute()
        )

        recipe_id = recipe_response.data[0]["id"]

        # ==========================================
        # SAVE INGREDIENTS
        # ==========================================

        for ingredient in processed_ingredients:

            (
                supabase.table("recipe_ingredients")
                .insert(
                    {
                        "recipe_id": recipe_id,
                        "organization_id": organization_id,
                        "ingredient_name": ingredient["ingredient"],
                        "canonical_product_id": None,
                        "quantity": ingredient["quantity"],
                        "unit": ingredient["unit"],
                        "cost": ingredient["cost"],
                    }
                )
                .execute()
            )

        # ==========================================
        # SUCCESS
        # ==========================================

        return {
            "success": True,
            "recipe_id": recipe_id,
            "recipe_name": recipe.get(
                "recipe_name",
                "Unknown Recipe",
            ),
            "servings": servings,
            "total_cost": round(total_cost, 2),
            "cost_per_serving": round(cost_per_serving, 2),
            "ingredients": processed_ingredients,
        }                 
   
    
    except Exception as e:
        print("RECIPE ERROR")
        print(e)

        return {
            "success": False,
            "error": str(e),
        }