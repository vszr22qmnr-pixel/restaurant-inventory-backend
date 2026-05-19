from fastapi import FastAPI, UploadFile
from openai import OpenAI
import base64
import os

app = FastAPI()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

@app.get("/")
async def root():

    return {
        "status": "Restaurant Inventory AI Backend Running"
    }

@app.post("/scan")
async def scan_inventory(file: UploadFile):

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
                        Identify all visible restaurant food inventory items.

                        Return ONLY valid JSON.

                        Format:

                        [
                          {
                            "product_name": "",
                            "estimated_quantity": 0,
                            "confidence": 0.0
                          }
                        ]

                        Include:
                        - product_name
                        - estimated_quantity
                        - confidence
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

    return {
        "result":
        response.choices[0].message.content
    }

@app.post("/scan_invoice")
async def scan_invoice(file: UploadFile):

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

                        Extract ALL visible information.

                        Return ONLY valid JSON.

                        Format:

                        {
                          "vendor_name": "",
                          "invoice_date": "",
                          "products": [
                            {
                              "product_name": "",
                              "quantity": "",
                              "price": 0
                            }
                          ]
                        }

                        Extract:
                        - vendor_name
                        - invoice_date
                        - products
                        - quantities
                        - prices
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

    return {
        "result":
        response.choices[0].message.content
    }