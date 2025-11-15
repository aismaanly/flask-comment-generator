import os
import torch
import re
from flask import Flask, render_template, request, jsonify
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel
from db import get_db, init_db, close_connection

from wordcloud import WordCloud
import matplotlib.pyplot as plt
import base64
from io import BytesIO

# ==== Load Model ====
base_model_name = "unsloth/llama-3.2-3b-bnb-4bit"
adapter_model_name = "aismaanly/ai_comment"

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=torch.bfloat16
)

print("Loading base model...")
model = AutoModelForCausalLM.from_pretrained(
    base_model_name,
    quantization_config=bnb_config,
    device_map="auto",
)
print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(base_model_name)
print("Loading adapter...")
model = PeftModel.from_pretrained(model, adapter_model_name)
model = model.merge_and_unload()
print("✅ Model loaded!")

# ==== Flask App ====
app = Flask(__name__)
app.teardown_appcontext(close_connection)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    input_text = data.get("input")

    formatted_prompt = f"""
Buat 3 komentar seperti pengguna sosial media remaja. Komentar harus santai, ekspresif, pakai emoji, dan gak formal. Gunakan campur bahasa Indonesia dan English slang.

Contoh:
Deskripsi Postingan:
Belanja lebih hemat dengan promo spesial dari kami! Dapatkan DISKON hingga 50% untuk produk pilihan terbaik yang bikin hari-harimu lebih berwarna. ✨Stok terbatas! Jangan sampai kehabisan kesempatan untuk belanja hemat

Komentar:
1. Gila diskonnya! Gue mau banget belanjaaaa! 😭🛍️  
2. Wishlist langsung auto check out 🔥  
3. Wahh ini sih gak boleh dilewatin! 🤯  

Deskripsi Postingan:
{input_text}
Komentar:
1."""

    input_ids = tokenizer(formatted_prompt, return_tensors="pt").to(model.device)

    outputs = model.generate(
        **input_ids,
        max_new_tokens=250,
        do_sample=True,
        temperature=0.7,
        top_p=0.9,
        repetition_penalty=1.1,
        pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
    )

    output_text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    raw_result = output_text[len(formatted_prompt):].strip()

    lines = raw_result.strip().split("\n")
    comments = []

    for line in lines:
        clean = re.sub(r"^\d+\.\s*", "", line).strip()
        if len(clean) > 5 and clean not in comments:
            comments.append(clean)

    comments = comments[:3]
    response_str = "\n".join(comments)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'INSERT INTO prompts (input, response, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
        (input_text, response_str)
    )
    db.commit()

    return jsonify({"comments": comments})

@app.route("/input")
def view_inputs():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, input, response FROM prompts ORDER BY id DESC')
    data = cursor.fetchall()
    return render_template("input.html", data=data)

@app.route("/delete_all", methods=["POST"])
def delete_all():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM prompts")
    db.commit()
    return jsonify({"status": "success"})

@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM prompts WHERE id = ?", (item_id,))
    db.commit()
    return jsonify({"status": "success"})

@app.route("/dashboard")
def dashboard():
    db = get_db()
    cursor = db.cursor()

    # === Data Word Frequency ===
    cursor.execute("SELECT response FROM prompts")
    responses = cursor.fetchall()

    all_comments = " ".join(response[0] for response in responses if response[0])
    words = re.findall(r'\b\w+\b', all_comments.lower())

    word_freq = {}
    for word in words:
        if len(word) > 3:
            word_freq[word] = word_freq.get(word, 0) + 1

    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:20]
    labels = [word for word, freq in sorted_words]
    values = [freq for word, freq in sorted_words]

    # === Generate WordCloud Image ===
    wc = WordCloud(width=800, height=400, background_color="black", colormap='Pastel1')
    wc.generate_from_frequencies(word_freq)
    img_io = BytesIO()
    wc.to_image().save(img_io, 'PNG')
    img_io.seek(0)
    img_base64 = base64.b64encode(img_io.read()).decode('utf-8')

    # === Data Trend per Hari ===
    cursor.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count 
        FROM prompts 
        GROUP BY DATE(created_at) 
        ORDER BY DATE(created_at)
    """)
    date_counts = cursor.fetchall()
    trend_dates = [row[0] for row in date_counts]
    trend_counts = [row[1] for row in date_counts]

    return render_template("dashboard.html",
        labels=labels,
        values=values,
        wordcloud=img_base64,
        trend_dates=trend_dates,
        trend_counts=trend_counts
    )

if __name__ == "__main__":
    init_db(app)
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, threaded=True)