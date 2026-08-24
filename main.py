import os
import requests
import re
import json
from supabase import create_client, Client
from datetime import datetime, timezone

# ==============================================================================
# 1. SETUP KREDENSIAL
# ==============================================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
YMS_COOKIE = os.environ.get("YMS_COOKIE")

# Pengecekan Pengaman
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ERROR CRITICAL: Kredensial Supabase tidak ditemukan!")
    print(f"Status SUPABASE_URL: {'TERBACA' if SUPABASE_URL else 'KOSONG (BELUM DI-SET)'}")
    print(f"Status SUPABASE_KEY: {'TERBACA' if SUPABASE_KEY else 'KOSONG (BELUM DI-SET)'}")
    print("Silakan periksa kembali Repository Secrets di GitHub Settings.")
    exit(1)

# Inisiasi Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==============================================================================
# 2. EKSEKUSI REQUEST KE WEBSITE
# ==============================================================================
url = "https://dashboard.wingscorp.com/yms/id/outbound?yard=NTM=&gate="
headers = {
    "Cookie": YMS_COOKIE,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

print("Memulai proses scraping YMS...")
response = requests.get(url, headers=headers)

# Debugging Info
print(f"Status Code: {response.status_code}")
print(f"Panjang HTML: {len(response.text)} karakter")

# Simpan HTML ke file lokal buat bahan investigasi kalau Regex gagal
with open("debug_yms.html", "w", encoding="utf-8") as f:
    f.write(response.text)

# ==============================================================================
# 3. EKSTRAKSI JSON TANPA REGEX (CARA BULLETPROOF)
# ==============================================================================
# Bersihkan karakter escape (\")
clean_html = response.text.replace('\\"', '"')

# Kita cari langsung kata kuncinya di dalam HTML
start_marker = '[{"licPlate"'
start_idx = clean_html.find(start_marker)

if start_idx != -1:
    # Ambil potongan teks dari awal array sampai 100.000 karakter ke depan (biar aman)
    chunk = clean_html[start_idx : start_idx + 100000]

    parsed_json = None
    # Brute-force: Cari kurung siku penutup ']' dari belakang ke depan
    for i in range(len(chunk), 10, -1):
        if chunk[i - 1] == "]":
            try:
                # Coba parse teksnya jadi JSON
                parsed_json = json.loads(chunk[:i])
                break  # Kalau sukses tanpa error, langsung STOP!
            except:
                pass  # Kalau error (berarti belum lengkap), lanjut potong lagi

    if parsed_json:
        print(f"🔥 BINGO! Berhasil menemukan {len(parsed_json)} data truk/shipment.")

        # ==========================================================================
        # 4. PEMBERSIHAN, DEDUPLIKASI, DAN FORMATTING DATA
        # ==========================================================================
        unique_data = {}  # Memakai dict agar key shipment otomatis unik

        for item in parsed_json:
            shipment_val = str(item.get("shipment", "")).strip()
            lic_plate_val = str(item.get("licPlate", "")).strip()

            # Jika shipment kosong/setrip, gunakan lic_plate sebagai kunci unik sementara
            unique_key = (
                shipment_val
                if shipment_val and shipment_val != "-"
                else f"NO_SHIP_{lic_plate_val}"
            )

            in_raw = item.get("in")
            out_raw = item.get("out")

            time_in = (
                datetime.fromtimestamp(in_raw / 1000.0, tz=timezone.utc).isoformat()
                if in_raw
                else None
            )
            time_out = (
                datetime.fromtimestamp(out_raw / 1000.0, tz=timezone.utc).isoformat()
                if out_raw
                else None
            )

            # Masukkan ke dictionary (jika ada key sama, otomatis yang paling baru menimpa yang lama)
            unique_data[unique_key] = {
                "shipment": unique_key,
                "yard_id": str(item.get("yardId", "")),
                "yard_name": str(item.get("yardName", "")),
                "lic_plate": lic_plate_val,
                "vehicle_type": str(item.get("vehicleType", "")),
                "gate": str(item.get("gate", "")),
                "time_in": time_in,
                "time_out": time_out,
                "status": "Scraped",
            }

        # Ubah kembali dictionary menjadi List
        data_to_insert = list(unique_data.values())

        # ==========================================================================
        # 5. SIMPAN ATAU UPDATE KE SUPABASE (UPSERT)
        # ==========================================================================
        try:
            result = (
                supabase.table("yms_data")
                .upsert(data_to_insert, on_conflict="shipment")
                .execute()
            )

            print(
                f"✅ SUKSES! {len(data_to_insert)} data berhasil di-UPSERT tanpa bentrok duplikat."
            )
        except Exception as e:
            print(f"❌ GAGAL menyimpan ke database Supabase. Error: {e}")

    else:
        print(
            "❌ GAGAL: Ketemu awalannya, tapi gagal dijadiin JSON. Coba cek file debug_yms.html."
        )
else:
    print(
        "❌ GAGAL: Awalan '[{\"licPlate\"' sama sekali nggak ketemu di HTML. Layout benar-benar berubah."
    )
