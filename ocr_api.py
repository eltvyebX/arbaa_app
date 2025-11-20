from fastapi import FastAPI, UploadFile, File
import requests

app = FastAPI()

OCR_API_KEY = "YOUR_OCR_SPACE_API_KEY"  # ضع هنا مفتاحك

@app.post("/upload/")
async def upload_image(file: UploadFile = File(...)):
    # اقرأ الصورة كـ bytes
    contents = await file.read()
    
    # استدعاء OCR.Space API
    response = requests.post(
        "https://api.ocr.space/parse/image",
        files={"filename": (file.filename, contents)},
        data={"apikey": OCR_API_KEY, "language": "eng"}  # يمكنك تغيير اللغة
    )
    
    result = response.json()
    
    # استخراج النص
    parsed_text = result.get("ParsedResults")[0].get("ParsedText") if result.get("ParsedResults") else ""
    
    return {"filename": file.filename, "text": parsed_text}
