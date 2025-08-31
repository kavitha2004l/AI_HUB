# main.py
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import FacebookPage, get_db, FacebookUser
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")
FB_REDIRECT_URI = os.getenv("FB_REDIRECT_URI")

if not all([FB_APP_ID, FB_APP_SECRET, FB_REDIRECT_URI]):
    logger.error("Missing environment variables: FB_APP_ID, FB_APP_SECRET, or FB_REDIRECT_URI")
    raise ValueError("Environment variables not properly set")

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/auth/facebook")
def login_facebook():
    fb_login_url = (
        f"https://www.facebook.com/v23.0/dialog/oauth"
        f"?client_id={FB_APP_ID}"
        f"&redirect_uri={FB_REDIRECT_URI}"
        f"&scope=pages_show_list,pages_messaging,"
        f"instagram_basic,instagram_manage_messages,"
        f"whatsapp_business_management,whatsapp_business_messaging,"
        f"business_management"
    )
    return RedirectResponse(fb_login_url)

# New endpoint for Embedded Signup
@app.get("/auth/facebook/signup-whatsapp")
def whatsapp_signup():
    signup_url = (
        f"https://www.facebook.com/v23.0/dialog/oauth"
        f"?client_id={FB_APP_ID}"
        f"&redirect_uri={FB_REDIRECT_URI}"
        f"&scope=whatsapp_business_management,business_management"
        f"&response_type=code"
        f"&extras={{'setup':{{'entry_point':'WHATSAPP_EMBEDDED_SIGNUP'}}}}"
    )
    logger.info("Initiating WhatsApp Embedded Signup")
    return RedirectResponse(signup_url)

@app.get("/auth/facebook/callback")
def facebook_callback(code: str = None, request: Request = None, db: Session = Depends(get_db)):
    if not code:
        logger.error("No code provided in Facebook callback")
        raise HTTPException(status_code=400, detail="No code provided")

    try:
        # 1. Exchange code for short-lived token
        token_response = requests.get(
            "https://graph.facebook.com/v23.0/oauth/access_token",
            params={
                "client_id": FB_APP_ID,
                "client_secret": FB_APP_SECRET,
                "redirect_uri": FB_REDIRECT_URI,
                "code": code
            }
        ).json()
        access_token = token_response.get("access_token")
        if not access_token:
            logger.error(f"Could not fetch short-lived token: {token_response}")
            raise HTTPException(status_code=400, detail=f"Could not fetch short-lived token: {token_response}")

        # 2. Exchange for long-lived token
        long_token_response = requests.get(
            "https://graph.facebook.com/v23.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": FB_APP_ID,
                "client_secret": FB_APP_SECRET,
                "fb_exchange_token": access_token
            }
        ).json()
        long_lived_token = long_token_response.get("access_token")
        if not long_lived_token:
            logger.error(f"Could not fetch long-lived token: {long_token_response}")
            raise HTTPException(status_code=400, detail=f"Could not fetch long-lived token: {long_token_response}")

        # 3. Get user info
        user_response = requests.get(
            "https://graph.facebook.com/v23.0/me",
            params={"access_token": long_lived_token}
        ).json()
        fb_user_id = user_response.get("id")
        if not fb_user_id:
            logger.error(f"Could not fetch user info: {user_response}")
            raise HTTPException(status_code=400, detail=f"Could not fetch user info: {user_response}")

        # 4. Check if user exists
        user = db.query(FacebookUser).filter(FacebookUser.fb_user_id == fb_user_id).first()
        if not user:
            user = FacebookUser(fb_user_id=fb_user_id, long_lived_token=long_lived_token)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.long_lived_token = long_lived_token
            db.commit()

        # 5. Fetch connected Facebook Pages
        pages_response = requests.get(
            "https://graph.facebook.com/v23.0/me/accounts",
            params={"access_token": long_lived_token}
        ).json()
        pages = pages_response.get("data", [])
        if not pages:
            logger.warning("No pages found for user")

        # 6. Fetch Instagram IDs linked to pages
        instagram_accounts = []
        for page in pages:
            ig_response = requests.get(
                f"https://graph.facebook.com/v23.0/{page['id']}",
                params={"fields": "instagram_business_account", "access_token": long_lived_token}
            ).json()
            ig_id = ig_response.get("instagram_business_account", {}).get("id")
            instagram_accounts.append({"page_id": page["id"], "instagram_id": ig_id})

        # 7. Fetch WhatsApp Business Account IDs
        whatsapp_accounts = []
        businesses_response = requests.get(
            "https://graph.facebook.com/v23.0/me/businesses",
            params={"access_token": long_lived_token}
        ).json()
        businesses = businesses_response.get("data", [])
        if businesses:
            business_id = businesses[0]["id"]
            waba_response = requests.get(
                f"https://graph.facebook.com/v23.0/{business_id}/owned_whatsapp_business_accounts",
                params={"access_token": long_lived_token}
            ).json()
            wabas = waba_response.get("data", [])
            for waba in wabas:
                waba_id = waba.get("id")
                phone_response = requests.get(
                    f"https://graph.facebook.com/v23.0/{waba_id}/phone_numbers",
                    params={"access_token": long_lived_token}
                ).json()
                phone_numbers = phone_response.get("data", [])
                whatsapp_accounts.append({
                    "waba_id": waba_id,
                    "phone_numbers": phone_numbers
                })
        else:
            logger.warning("No WhatsApp Business Accounts found")
            # Redirect to Embedded Signup if no WABA found
            return RedirectResponse(url="/auth/facebook/signup-whatsapp")

        # 8. Save pages and linked accounts
        for page in pages:
            try:
                page_obj = db.query(FacebookPage).filter(FacebookPage.page_id == page["id"]).first()
                if not page_obj:
                    page_obj = FacebookPage(
                        page_id=page["id"],
                        page_name=page["name"],
                        page_access_token=page.get("access_token"),
                        user_id=user.id
                    )
                else:
                    page_obj.page_access_token = page.get("access_token")
                # Add Instagram ID
                ig = next((i for i in instagram_accounts if i["page_id"] == page["id"]), None)
                if ig:
                    page_obj.instagram_id = ig["instagram_id"]

                # Add WhatsApp (assume shared; use first WABA/phone if available)
                if whatsapp_accounts:
                    page_obj.whatsapp_id = whatsapp_accounts[0]["waba_id"]
                    if whatsapp_accounts[0]["phone_numbers"]:
                        page_obj.whatsapp_phone_number_id = whatsapp_accounts[0]["phone_numbers"][0]["id"]

                db.add(page_obj)
                db.commit()
            except Exception as e:
                logger.error(f"Error saving page {page['id']}: {str(e)}")
                db.rollback()
                continue

        # 9. Render dashboard
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "fb_user_id": fb_user_id,
                "pages": pages,
                "instagram_accounts": instagram_accounts,
                "whatsapp_accounts": whatsapp_accounts
            }
        )
    except Exception as e:
        logger.error(f"Error in facebook_callback: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Optional: Test endpoint for sending WhatsApp message
@app.post("/test-whatsapp-message")
async def test_whatsapp_message(phone_number_id: str, recipient: str, db: Session = Depends(get_db)):
    user = db.query(FacebookUser).first()
    if not user:
        raise HTTPException(status_code=404, detail="No user found")
    try:
        response = requests.post(
            f"https://graph.facebook.com/v23.0/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {user.long_lived_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"body": "Test message from AI Hub Bot!"}
            }
        ).json()
        return response
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to send message: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)