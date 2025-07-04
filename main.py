from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from werkzeug.utils import secure_filename
import os
import sqlite3
from datetime import datetime
import zipfile
import io
import qrcode
from PIL import Image
import uuid
import threading
import time
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Güvenlik için değiştirin

# Konfigürasyon
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'webp'}
MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Klasörleri oluştur
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('static', exist_ok=True)
os.makedirs('templates', exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def init_db():
    conn = sqlite3.connect('wedding_photos.db')
    c = conn.cursor()

    # Fotoğraflar tablosu
    c.execute('''
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uploader_name TEXT,
            file_size INTEGER
        )
    ''')

    # Site ayarları tablosu
    c.execute('''
        CREATE TABLE IF NOT EXISTS site_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE NOT NULL,
            setting_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Varsayılan ayarları ekle
    default_settings = [
        ('site_title', 'BYT DIGITAL'),
        ('site_description', 'Anılarınızı Paylaşın'),
        ('site_emoji', '💒❤️'),
        ('background_image', ''),
        ('background_opacity', '0.1'),
        ('container_background_image', ''),
        ('container_background_opacity', '0.2'),
        ('copyright_text', 'BYT DIGITAL © 2025 - TÜM HAKLARI SAKLIDIR')
    ]

    for key, value in default_settings:
        c.execute('''
            INSERT OR IGNORE INTO site_settings (setting_key, setting_value)
            VALUES (?, ?)
        ''', (key, value))

    conn.commit()
    conn.close()


def get_db_connection():
    conn = sqlite3.connect('wedding_photos.db')
    conn.row_factory = sqlite3.Row
    return conn


def get_site_settings():
    """Site ayarlarını veritabanından al"""
    conn = get_db_connection()
    settings = conn.execute('SELECT setting_key, setting_value FROM site_settings').fetchall()
    conn.close()

    # Dictionary'ye çevir
    settings_dict = {}
    for setting in settings:
        settings_dict[setting['setting_key']] = setting['setting_value']

    # Varsayılan değerler
    if 'site_title' not in settings_dict:
        settings_dict['site_title'] = 'BYT DIGITAL'
    if 'site_description' not in settings_dict:
        settings_dict['site_description'] = 'Anılarınızı Paylaşın'
    if 'site_emoji' not in settings_dict:
        settings_dict['site_emoji'] = '💒❤️'
    if 'background_image' not in settings_dict:
        settings_dict['background_image'] = ''
    if 'background_opacity' not in settings_dict:
        settings_dict['background_opacity'] = '0.1'
    if 'container_background_image' not in settings_dict:
        settings_dict['container_background_image'] = ''
    if 'container_background_opacity' not in settings_dict:
        settings_dict['container_background_opacity'] = '0.2'
    if 'copyright_text' not in settings_dict:
        settings_dict['copyright_text'] = 'BYT DIGITAL © 2025 - TÜM HAKLARI SAKLIDIR'

    return settings_dict


def update_site_setting(key, value):
    """Site ayarını güncelle"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO site_settings (setting_key, setting_value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', (key, value))
    conn.commit()
    conn.close()


@app.route('/')
def upload_form():
    settings = get_site_settings()
    return render_template('upload.html', settings=settings)


@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        if 'photos' not in request.files:
            return jsonify({'error': 'Fotoğraf seçilmedi'}), 400

        files = request.files.getlist('photos')
        uploader_name = request.form.get('uploader_name', 'Anonim')

        if not files or files[0].filename == '':
            return jsonify({'error': 'Fotoğraf seçilmedi'}), 400

        uploaded_count = 0
        errors = []

        conn = get_db_connection()

        for file in files:
            if file and allowed_file(file.filename):
                try:
                    # Güvenli dosya adı oluştur
                    original_filename = file.filename
                    file_extension = original_filename.rsplit('.', 1)[1].lower()
                    unique_filename = f"{uuid.uuid4()}.{file_extension}"

                    # Dosyayı kaydet
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(file_path)

                    # Dosya boyutunu al
                    file_size = os.path.getsize(file_path)

                    # Veritabanına kaydet
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO photos (filename, original_filename, uploader_name, file_size)
                        VALUES (?, ?, ?, ?)
                    ''', (unique_filename, original_filename, uploader_name, file_size))

                    uploaded_count += 1

                except Exception as e:
                    errors.append(f"{original_filename}: {str(e)}")
            else:
                errors.append(f"{file.filename}: Geçersiz dosya formatı")

        conn.commit()
        conn.close()

        response_data = {
            'uploaded_count': uploaded_count,
            'total_files': len(files)
        }

        if errors:
            response_data['errors'] = errors

        return jsonify(response_data)

    except Exception as e:
        return jsonify({'error': f'Yükleme hatası: {str(e)}'}), 500


@app.route('/admin')
def admin_panel():
    conn = get_db_connection()
    photos = conn.execute('''
        SELECT * FROM photos ORDER BY upload_time DESC
    ''').fetchall()

    # Yükleyenlere göre gruplandır
    grouped_photos = defaultdict(list)
    uploader_stats = defaultdict(lambda: {'count': 0, 'size': 0, 'latest': None})

    for photo in photos:
        uploader = photo['uploader_name'] or 'Anonim'
        grouped_photos[uploader].append(photo)

        # İstatistikleri güncelle
        uploader_stats[uploader]['count'] += 1
        uploader_stats[uploader]['size'] += photo['file_size']

        if not uploader_stats[uploader]['latest'] or photo['upload_time'] > uploader_stats[uploader]['latest']:
            uploader_stats[uploader]['latest'] = photo['upload_time']

    total_photos = len(photos)
    total_size = sum(photo['file_size'] for photo in photos)
    total_size_mb = round(total_size / (1024 * 1024), 2)

    conn.close()

    # Site ayarlarını al
    settings = get_site_settings()

    return render_template('admin.html',
                           grouped_photos=dict(grouped_photos),
                           uploader_stats=dict(uploader_stats),
                           total_photos=total_photos,
                           total_size_mb=total_size_mb,
                           total_uploaders=len(grouped_photos),
                           settings=settings)


@app.route('/admin/delete_background')
def delete_background():
    """Sayfa arka plan resmini sil"""
    try:
        settings = get_site_settings()
        old_bg = settings.get('background_image', '')

        if old_bg:
            old_path = os.path.join('static', old_bg)
            if os.path.exists(old_path):
                os.remove(old_path)

            update_site_setting('background_image', '')
            flash('Sayfa arka plan resmi başarıyla silindi!', 'success')

            # Template'leri yeniden oluştur
            create_templates()
        else:
            flash('Silinecek sayfa arka plan resmi bulunamadı.', 'error')

    except Exception as e:
        flash(f'Sayfa arka plan resmi silinirken hata oluştu: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))


@app.route('/admin/delete_container_background')
def delete_container_background():
    """Container arka plan resmini sil"""
    try:
        settings = get_site_settings()
        old_bg = settings.get('container_background_image', '')

        if old_bg:
            old_path = os.path.join('static', old_bg)
            if os.path.exists(old_path):
                os.remove(old_path)

            update_site_setting('container_background_image', '')
            flash('Container arka plan resmi başarıyla silindi!', 'success')

            # Template'leri yeniden oluştur
            create_templates()
        else:
            flash('Silinecek container arka plan resmi bulunamadı.', 'error')

    except Exception as e:
        flash(f'Container arka plan resmi silinirken hata oluştu: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))


@app.route('/admin/settings', methods=['POST'])
def update_settings():
    """Site ayarlarını güncelle"""
    try:
        site_title = request.form.get('site_title', '').strip()
        site_description = request.form.get('site_description', '').strip()
        site_emoji = request.form.get('site_emoji', '').strip()
        background_opacity = request.form.get('background_opacity', '0.1')
        container_background_opacity = request.form.get('container_background_opacity', '0.2')
        footer_text = request.form.get('footer_text', '').strip()
        copyright_text = request.form.get('copyright_text', '').strip()

        if not site_title:
            flash('Site başlığı boş olamaz.', 'error')
            return redirect(url_for('admin_panel'))

        # Arka plan resmi yükleme kontrolü
        background_image_filename = None
        if 'background_image' in request.files:
            background_file = request.files['background_image']
            if background_file and background_file.filename != '':
                if allowed_file(background_file.filename):
                    # Güvenli dosya adı oluştur
                    file_extension = background_file.filename.rsplit('.', 1)[1].lower()
                    background_image_filename = f"background_{uuid.uuid4()}.{file_extension}"

                    # Eski arka plan resmini sil
                    settings = get_site_settings()
                    old_bg = settings.get('background_image', '')
                    if old_bg:
                        old_path = os.path.join('static', old_bg)
                        if os.path.exists(old_path):
                            os.remove(old_path)

                    # Yeni resmi kaydet
                    background_path = os.path.join('static', background_image_filename)
                    background_file.save(background_path)

                    # Ayarı güncelle
                    update_site_setting('background_image', background_image_filename)
                else:
                    flash('Geçersiz resim formatı. JPG, PNG, GIF, HEIC, WebP desteklenir.', 'error')
                    return redirect(url_for('admin_panel'))

        # Container arka plan resmi yükleme kontrolü
        container_background_filename = None
        if 'container_background_image' in request.files:
            container_background_file = request.files['container_background_image']
            if container_background_file and container_background_file.filename != '':
                if allowed_file(container_background_file.filename):
                    # Güvenli dosya adı oluştur
                    file_extension = container_background_file.filename.rsplit('.', 1)[1].lower()
                    container_background_filename = f"container_bg_{uuid.uuid4()}.{file_extension}"

                    # Eski container arka plan resmini sil
                    settings = get_site_settings()
                    old_container_bg = settings.get('container_background_image', '')
                    if old_container_bg:
                        old_path = os.path.join('static', old_container_bg)
                        if os.path.exists(old_path):
                            os.remove(old_path)

                    # Yeni resmi kaydet
                    container_background_path = os.path.join('static', container_background_filename)
                    container_background_file.save(container_background_path)

                    # Ayarı güncelle
                    update_site_setting('container_background_image', container_background_filename)
                else:
                    flash('Geçersiz resim formatı. JPG, PNG, GIF, HEIC, WebP desteklenir.', 'error')
                    return redirect(url_for('admin_panel'))

        # Diğer ayarları güncelle
        update_site_setting('site_title', site_title)
        update_site_setting('site_description', site_description)
        update_site_setting('site_emoji', site_emoji)
        update_site_setting('background_opacity', background_opacity)
        update_site_setting('container_background_opacity', container_background_opacity)
        if footer_text:
            update_site_setting('footer_text', footer_text)
        if copyright_text:
            update_site_setting('copyright_text', copyright_text)

        flash('Site ayarları başarıyla güncellendi!', 'success')

        # Template'leri yeniden oluştur
        create_templates()

    except Exception as e:
        flash(f'Ayarlar güncellenirken hata oluştu: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))


@app.route('/download_all')
def download_all():
    try:
        conn = get_db_connection()
        photos = conn.execute('SELECT * FROM photos ORDER BY upload_time').fetchall()
        conn.close()

        if not photos:
            flash('İndirilecek fotoğraf bulunamadı.')
            return redirect(url_for('admin_panel'))

        # ZIP dosyası oluştur
        memory_file = io.BytesIO()

        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for photo in photos:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
                if os.path.exists(file_path):
                    # Dosyayı ZIP'e ekle (orijinal adıyla)
                    upload_time = datetime.strptime(photo['upload_time'], '%Y-%m-%d %H:%M:%S')
                    time_str = upload_time.strftime('%Y%m%d_%H%M%S')
                    archive_name = f"{time_str}_{photo['uploader_name']}_{photo['original_filename']}"
                    zf.write(file_path, archive_name)

        memory_file.seek(0)

        today = datetime.now().strftime('%Y%m%d')
        filename = f"BYT_DIGITAL_Dugun_Fotograflari_{today}.zip"

        return send_file(memory_file,
                         as_attachment=True,
                         download_name=filename,
                         mimetype='application/zip')

    except Exception as e:
        flash(f'ZIP oluşturma hatası: {str(e)}')
        return redirect(url_for('admin_panel'))


# Belirli bir yükleyenin fotoğraflarını indirme
@app.route('/download_uploader/<uploader_name>')
def download_uploader_photos(uploader_name):
    try:
        conn = get_db_connection()
        photos = conn.execute('''
            SELECT * FROM photos 
            WHERE uploader_name = ? OR (uploader_name IS NULL AND ? = 'Anonim')
            ORDER BY upload_time
        ''', (uploader_name if uploader_name != 'Anonim' else None, uploader_name)).fetchall()
        conn.close()

        if not photos:
            flash('İndirilecek fotoğraf bulunamadı.')
            return redirect(url_for('admin_panel'))

        # ZIP dosyası oluştur
        memory_file = io.BytesIO()

        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for photo in photos:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
                if os.path.exists(file_path):
                    # Dosyayı ZIP'e ekle (orijinal adıyla)
                    upload_time = datetime.strptime(photo['upload_time'], '%Y-%m-%d %H:%M:%S')
                    time_str = upload_time.strftime('%Y%m%d_%H%M%S')
                    archive_name = f"{time_str}_{photo['original_filename']}"
                    zf.write(file_path, archive_name)

        memory_file.seek(0)

        today = datetime.now().strftime('%Y%m%d')
        safe_uploader_name = uploader_name.replace(' ', '_').replace('/', '_')
        filename = f"BYT_DIGITAL_{safe_uploader_name}_{today}.zip"

        return send_file(memory_file,
                         as_attachment=True,
                         download_name=filename,
                         mimetype='application/zip')

    except Exception as e:
        flash(f'ZIP oluşturma hatası: {str(e)}')
        return redirect(url_for('admin_panel'))


@app.route('/qr')
def generate_qr():
    # QR kod için URL (gerçek domain ile değiştirin)
    url = request.url_root

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # QR kodu kaydet
    qr_path = 'static/qr_code.png'
    img.save(qr_path)

    return send_file(qr_path, as_attachment=True, download_name='dugun_qr_kod.png')


@app.route('/stats')
def get_stats():
    conn = get_db_connection()

    total_photos = conn.execute('SELECT COUNT(*) as count FROM photos').fetchone()['count']

    recent_uploads = conn.execute('''
        SELECT COUNT(*) as count FROM photos 
        WHERE upload_time > datetime('now', '-1 hour')
    ''').fetchone()['count']

    uploaders = conn.execute('''
        SELECT uploader_name, COUNT(*) as count 
        FROM photos 
        GROUP BY uploader_name 
        ORDER BY count DESC
    ''').fetchall()

    conn.close()

    return jsonify({
        'total_photos': total_photos,
        'recent_uploads': recent_uploads,
        'top_uploaders': [dict(uploader) for uploader in uploaders[:5]]
    })


# HTML Templates oluştur
def create_templates():
    # Site ayarlarını al
    settings = get_site_settings()

    # Upload template - El yazısı fontlarıyla optimize edilmiş
    upload_html = f'''<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{settings['site_emoji']} {settings['site_title']} - Düğün Fotoğrafları</title>
    <!-- Google Fonts - El Yazısı Fontları -->
    <link href="https://fonts.googleapis.com/css2?family=Dancing+Script:wght@400;500;600;700&family=Caveat:wght@400;500;600;700&family=Kalam:wght@300;400;700&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        html, body {{
            min-height: 100vh;
        }}

        body {{
            font-family: 'Caveat', 'Dancing Script', cursive;
            background: transparent;
            padding: 10px;
            position: relative;
            display: flex;
            align-items: flex-start;
            justify-content: center;
            padding-top: 15px;
            min-height: 100vh;
            font-size: 16px; /* El yazısı fontları için biraz daha büyük */
        }}

        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -2;
            {f'background-image: url("/static/{settings["background_image"]}");' if settings.get("background_image") else ""}
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            opacity: {settings.get("background_opacity", "0.1")};
        }}

        .container {{
            max-width: 720px;
            width: 100%;
            min-height: auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.15);
            position: relative;
            backdrop-filter: blur(15px);
            display: flex;
            flex-direction: column;
            margin-bottom: 20px;
        }}

        .container::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            {f'background-image: url("/static/{settings["container_background_image"]}");' if settings.get("container_background_image") else f'background-image: url("/static/{settings["background_image"]}");' if settings.get("background_image") else ""}
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            opacity: {settings.get("container_background_opacity", "0.2") if settings.get("container_background_image") else float(settings.get("background_opacity", "0.1")) * 0.3};
            border-radius: 20px;
        }}

        .header {{
            background: transparent;
            color: white;
            padding: 20px 25px;
            text-align: center;
            flex-shrink: 0;
            position: relative;
        }}

        .header h1 {{
            font-family: 'Dancing Script', cursive;
            font-size: 2.2rem; /* El yazısı için daha büyük */
            margin-bottom: 12px;
            font-weight: 700;
            text-shadow: 3px 3px 8px rgba(0, 0, 0, 0.8), 1px 1px 4px rgba(0, 0, 0, 0.9);
            position: relative;
            z-index: 1;
            letter-spacing: 1px;
            line-height: 1.2;
        }}

        .header p {{
            font-family: 'Caveat', cursive;
            font-size: 1.1rem;
            opacity: 0.95;
            white-space: pre-line;
            line-height: 1.4;
            text-shadow: 2px 2px 6px rgba(0, 0, 0, 0.8), 1px 1px 3px rgba(0, 0, 0, 0.9);
            position: relative;
            z-index: 1;
            font-weight: 500;
        }}

        .form-container {{
            padding: 20px 30px 15px 30px;
            flex: 1;
            display: flex;
            flex-direction: column;
        }}

        .form-content {{
            flex: 1;
            padding-right: 5px;
        }}

        .form-group {{
            margin-bottom: 18px;
            flex-shrink: 0;
        }}

        label {{
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
            color: #333;
            font-size: 1.1rem;
            font-family: 'Kalam', cursive;
        }}

        input[type="text"] {{
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 1rem;
            transition: all 0.3s ease;
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(10px);
            font-family: 'Caveat', cursive;
            font-weight: 500;
        }}

        input[type="text"]:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            background: rgba(255, 255, 255, 0.95);
        }}

        .file-upload-area {{
            border: 3px dashed #ccc;
            border-radius: 12px;
            padding: 18px 14px;
            text-align: center;
            transition: all 0.3s ease;
            cursor: pointer;
            background: rgba(248, 249, 250, 0.8);
            backdrop-filter: blur(10px);
            position: relative;
            overflow: hidden;
            max-width: 260px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            min-height: 160px;
        }}

        .file-upload-area::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.05) 100%);
            border-radius: 15px;
            z-index: -1;
        }}

        .file-upload-area:hover {{
            border-color: #667eea;
            background: rgba(240, 242, 255, 0.9);
            transform: translateY(-2px) scale(1.02);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.15);
        }}

        .file-upload-area.dragover {{
            border-color: #667eea;
            background: rgba(240, 242, 255, 0.9);
            transform: scale(1.05);
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.2);
        }}

        .upload-icon {{
            font-size: 2.5rem;
            margin-bottom: 8px;
            color: #667eea;
            filter: drop-shadow(2px 2px 4px rgba(0,0,0,0.1));
        }}

        .upload-text {{
            font-size: 1rem;
            color: #333;
            margin-bottom: 5px;
            font-weight: 600;
            line-height: 1.3;
            font-family: 'Kalam', cursive;
        }}

        .upload-subtext {{
            color: #666;
            font-size: 0.85rem;
            margin-bottom: 3px;
            opacity: 0.8;
            line-height: 1.2;
            font-family: 'Caveat', cursive;
        }}

        #photoInput {{
            display: none;
        }}

        .btn {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 16px 36px;
            border: none;
            border-radius: 12px;
            font-size: 1.2rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s ease;
            width: 100%;
            margin-top: 16px;
            position: relative;
            overflow: hidden;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.3);
            font-family: 'Kalam', cursive;
        }}

        .btn::before {{
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
            transition: left 0.5s;
        }}

        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
        }}

        .btn:hover::before {{
            left: 100%;
        }}

        .btn:active {{
            transform: translateY(-1px);
        }}

        .btn:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
            box-shadow: 0 3px 12px rgba(102, 126, 234, 0.2);
        }}

        .btn:disabled::before {{
            display: none;
        }}

        .progress-container {{
            margin-top: 18px;
            display: none;
        }}

        .progress-bar {{
            width: 100%;
            height: 18px;
            background: #e0e0e0;
            border-radius: 9px;
            overflow: hidden;
        }}

        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #667eea, #764ba2);
            width: 0%;
            transition: width 0.3s;
        }}

        .progress-text {{
            text-align: center;
            margin-top: 10px;
            font-weight: 600;
            color: #333;
            font-size: 0.95rem;
            font-family: 'Caveat', cursive;
        }}

        .selected-files {{
            margin-top: 18px;
            padding: 18px;
            background: rgba(248, 249, 250, 0.9);
            border-radius: 10px;
            display: none;
            border: 1px solid #e9ecef;
            font-size: 0.95rem;
            font-family: 'Caveat', cursive;
        }}

        .file-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid #e0e0e0;
        }}

        .file-item:last-child {{
            border-bottom: none;
        }}

        .success-message, .error-message {{
            padding: 14px;
            border-radius: 10px;
            margin-top: 18px;
            display: none;
            font-weight: 600;
            font-size: 0.95rem;
            font-family: 'Kalam', cursive;
        }}

        .success-message {{
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }}

        .error-message {{
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }}

        .footer {{
            text-align: center;
            padding: 18px;
            color: #666;
            font-size: 0.85rem;
            font-family: 'Caveat', cursive;
        }}

        /* Desktop uyumluluğu için media queries */
        @media (min-width: 768px) {{
            body {{
                padding-top: 25px;
                font-size: 18px;
            }}

            .header h1 {{
                font-size: 2.8rem;
            }}

            .header p {{
                font-size: 1.3rem;
            }}

            .file-upload-area {{
                max-width: 300px;
                min-height: 200px;
                padding: 24px 18px;
            }}

            .upload-icon {{
                font-size: 3.2rem;
            }}

            .upload-text {{
                font-size: 1.2rem;
            }}

            .upload-subtext {{
                font-size: 1rem;
            }}

            label {{
                font-size: 1.25rem;
            }}

            input[type="text"] {{
                font-size: 1.1rem;
                padding: 16px 18px;
            }}

            .btn {{
                font-size: 1.3rem;
                padding: 18px 40px;
            }}
        }}

        @media (max-width: 767px) {{
            body {{
                padding: 5px;
                padding-top: 12px;
                font-size: 15px;
            }}

            .container {{
                border-radius: 15px;
                margin-bottom: 12px;
            }}

            .container::before {{
                border-radius: 15px;
            }}

            .header h1 {{
                font-size: 1.8rem;
            }}

            .header p {{
                font-size: 1rem;
            }}

            .header {{
                padding: 15px 15px;
            }}

            .form-container {{
                padding: 15px 18px 10px 18px;
            }}

            .file-upload-area {{
                padding: 15px 10px;
                max-width: 200px;
                min-height: 140px;
                aspect-ratio: 1;
            }}

            .upload-icon {{
                font-size: 2rem;
            }}

            .upload-text {{
                font-size: 0.9rem;
            }}

            .upload-subtext {{
                font-size: 0.75rem;
            }}

            label {{
                font-size: 1rem;
            }}

            input[type="text"] {{
                font-size: 0.95rem;
                padding: 12px 14px;
            }}

            .btn {{
                font-size: 1.1rem;
                padding: 14px 32px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{settings['site_emoji']} {settings['site_title']}</h1>
            <p>{settings['site_description']}</p>
        </div>

        <div class="form-container">
            <form id="uploadForm" enctype="multipart/form-data">
                <div class="form-content">
                    <div class="form-group">
                        <label for="uploaderName">📝 Adınız (İsteğe bağlı)</label>
                        <input type="text" id="uploaderName" name="uploader_name" placeholder="Adınızı yazın...">
                    </div>

                    <div class="form-group">
                        <label>📸 Fotoğrafları Seçin</label>
                        <div class="file-upload-area" onclick="document.getElementById('photoInput').click()">
                            <div class="upload-icon">📷</div>
                            <div class="upload-text">Fotoğrafları yüklemek için tıklayın</div>
                            <div class="upload-subtext">veya dosyaları buraya sürükleyip bırakın</div>
                            <div class="upload-subtext">JPG, PNG, HEIC, WebP desteklenir</div>
                        </div>
                        <input type="file" id="photoInput" name="photos" multiple accept="image/*">
                    </div>

                    <button type="submit" class="btn" id="uploadBtn">
                        🚀 Fotoğrafları Yükle
                    </button>

                    <div class="selected-files" id="selectedFiles"></div>

                    <div class="progress-container" id="progressContainer">
                        <div class="progress-bar">
                            <div class="progress-fill" id="progressFill"></div>
                        </div>
                        <div class="progress-text" id="progressText">Yükleniyor...</div>
                    </div>

                    <div class="success-message" id="successMessage"></div>
                    <div class="error-message" id="errorMessage"></div>
                </div>
            </form>
        </div>

        <div class="footer">
            <p style="font-size: 0.8rem; margin-top: 5px; opacity: 0.7; font-weight: 600;">{settings.get('copyright_text', 'BYT DIGITAL © 2025 - TÜM HAKLARI SAKLIDIR')}</p>
        </div>
    </div>

    <script>
        const photoInput = document.getElementById('photoInput');
        const selectedFiles = document.getElementById('selectedFiles');
        const uploadForm = document.getElementById('uploadForm');
        const uploadBtn = document.getElementById('uploadBtn');
        const progressContainer = document.getElementById('progressContainer');
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        const successMessage = document.getElementById('successMessage');
        const errorMessage = document.getElementById('errorMessage');
        const fileUploadArea = document.querySelector('.file-upload-area');

        let selectedFilesList = [];

        // Drag and Drop
        fileUploadArea.addEventListener('dragover', (e) => {{
            e.preventDefault();
            fileUploadArea.classList.add('dragover');
        }});

        fileUploadArea.addEventListener('dragleave', (e) => {{
            e.preventDefault();
            fileUploadArea.classList.remove('dragover');
        }});

        fileUploadArea.addEventListener('drop', (e) => {{
            e.preventDefault();
            fileUploadArea.classList.remove('dragover');
            const files = Array.from(e.dataTransfer.files);
            handleFileSelection(files);
        }});

        photoInput.addEventListener('change', (e) => {{
            const files = Array.from(e.target.files);
            handleFileSelection(files);
        }});

        function handleFileSelection(files) {{
            selectedFilesList = files.filter(file => file.type.startsWith('image/'));
            displaySelectedFiles();
        }}

        function displaySelectedFiles() {{
            if (selectedFilesList.length === 0) {{
                selectedFiles.style.display = 'none';
                return;
            }}

            selectedFiles.style.display = 'block';
            const totalSize = selectedFilesList.reduce((sum, file) => sum + file.size, 0);
            const totalSizeMB = (totalSize / 1024 / 1024).toFixed(2);

            selectedFiles.innerHTML = `
                📁 ${{selectedFilesList.length}} fotoğraf seçildi (Toplam: ${{totalSizeMB}} MB)
            `;
        }}

        uploadForm.addEventListener('submit', async (e) => {{
            e.preventDefault();

            if (selectedFilesList.length === 0) {{
                showError('Lütfen en az bir fotoğraf seçin.');
                return;
            }}

            const formData = new FormData();
            const uploaderName = document.getElementById('uploaderName').value || 'Anonim';

            formData.append('uploader_name', uploaderName);
            selectedFilesList.forEach(file => {{
                formData.append('photos', file);
            }});

            uploadBtn.disabled = true;
            progressContainer.style.display = 'block';
            hideMessages();

            try {{
                const response = await fetch('/upload', {{
                    method: 'POST',
                    body: formData
                }});

                const result = await response.json();

                if (response.ok) {{
                    showSuccess(`✅ ${{result.uploaded_count}} fotoğraf başarıyla yüklendi!`);
                    uploadForm.reset();
                    selectedFilesList = [];
                    displaySelectedFiles();
                }} else {{
                    showError(result.error || 'Yükleme sırasında hata oluştu.');
                }}
            }} catch (error) {{
                showError('Ağ hatası oluştu. Lütfen tekrar deneyin.');
            }} finally {{
                uploadBtn.disabled = false;
                progressContainer.style.display = 'none';
                progressFill.style.width = '0%';
            }}
        }});

        function showSuccess(message) {{
            successMessage.textContent = message;
            successMessage.style.display = 'block';
            errorMessage.style.display = 'none';
        }}

        function showError(message) {{
            errorMessage.textContent = message;
            errorMessage.style.display = 'block';
            successMessage.style.display = 'none';
        }}

        function hideMessages() {{
            successMessage.style.display = 'none';
            errorMessage.style.display = 'none';
        }}
    </script>
</body>
</html>'''

    # Admin template el yazısı fontlarıyla %75 zoom
    admin_html = '''<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ settings.site_title }} - Admin Panel</title>
    <!-- Google Fonts - El Yazısı Fontları -->
    <link href="https://fonts.googleapis.com/css2?family=Dancing+Script:wght@400;500;600;700&family=Caveat:wght@400;500;600;700&family=Kalam:wght@300;400;700&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Caveat', 'Dancing Script', cursive;
            background: #f5f7fa;
            padding: 15px; /* 20px'in %75'i */
            font-size: 12px; /* 16px'in %75'i */
        }

        .container {
            max-width: 900px; /* 1200px'in %75'i */
            margin: 0 auto;
        }

        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 23px; /* 30px'in %75'i */
            border-radius: 11px; /* 15px'in %75'i */
            margin-bottom: 23px; /* 30px'in %75'i */
            text-align: center;
        }

        .header h1 {
            font-family: 'Dancing Script', cursive;
            font-size: 1.87rem; /* 2.5rem'in %75'i */
            font-weight: 700;
            margin-bottom: 8px; /* 10px'in %75'i */
            letter-spacing: 0.75px; /* 1px'in %75'i */
        }

        .header h2 {
            font-family: 'Caveat', cursive;
            font-size: 1.12rem; /* 1.5rem'in %75'i */
            font-weight: 500;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(188px, 1fr)); /* 250px'in %75'i */
            gap: 15px; /* 20px'in %75'i */
            margin-bottom: 23px; /* 30px'in %75'i */
        }

        .stat-card {
            background: white;
            padding: 19px; /* 25px'in %75'i */
            border-radius: 11px; /* 15px'in %75'i */
            box-shadow: 0 4px 11px rgba(0,0,0,0.08); /* %75 oranında */
            text-align: center;
        }

        .stat-number {
            font-size: 1.65rem; /* 2.2rem'in %75'i */
            font-weight: bold;
            color: #667eea;
            margin-bottom: 8px; /* 10px'in %75'i */
            font-family: 'Kalam', cursive;
        }

        .stat-label {
            color: #666;
            font-size: 0.82rem; /* 1.1rem'in %75'i */
            font-family: 'Caveat', cursive;
            font-weight: 500;
        }

        .actions {
            background: white;
            padding: 19px; /* 25px'in %75'i */
            border-radius: 11px; /* 15px'in %75'i */
            box-shadow: 0 4px 11px rgba(0,0,0,0.08); /* %75 oranında */
            margin-bottom: 23px; /* 30px'in %75'i */
            text-align: center;
        }

        .settings-section {
            background: white;
            padding: 19px; /* 25px'in %75'i */
            border-radius: 11px; /* 15px'in %75'i */
            box-shadow: 0 4px 11px rgba(0,0,0,0.08); /* %75 oranında */
            margin-bottom: 23px; /* 30px'in %75'i */
        }

        .settings-form {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(195px, 1fr)); /* 260px'in %75'i */
            gap: 15px; /* 20px'in %75'i */
            margin-top: 15px; /* 20px'in %75'i */
        }

        .form-group {
            margin-bottom: 11px; /* 15px'in %75'i */
        }

        .form-group label {
            display: block;
            margin-bottom: 6px; /* 8px'in %75'i */
            font-weight: 600;
            color: #333;
            font-family: 'Kalam', cursive;
            font-size: 0.82rem; /* 1.1rem'in %75'i */
        }

        .form-group input,
        .form-group textarea {
            width: 100%;
            padding: 9px; /* 12px'in %75'i */
            border: 2px solid #e0e0e0;
            border-radius: 6px; /* 8px'in %75'i */
            font-size: 0.75rem; /* 1rem'in %75'i */
            transition: border-color 0.3s;
            font-family: 'Caveat', cursive;
            font-weight: 500;
        }

        .form-group input:focus,
        .form-group textarea:focus {
            outline: none;
            border-color: #667eea;
        }

        .form-group textarea {
            resize: vertical;
            min-height: 60px; /* 80px'in %75'i */
            line-height: 1.5;
        }

        .emoji-input {
            font-size: 0.98rem; /* 1.3rem'in %75'i */
            text-align: center;
            letter-spacing: 1.5px; /* 2px'in %75'i */
            background: linear-gradient(45deg, #f8f9fa, #ffffff);
            border: 2px dashed #dee2e6 !important;
        }

        .emoji-input:focus {
            border: 2px dashed #667eea !important;
            background: linear-gradient(45deg, #f0f2ff, #ffffff);
        }

        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 9px 19px; /* 12px 25px'in %75'i */
            border: none;
            border-radius: 6px; /* 8px'in %75'i */
            text-decoration: none;
            display: inline-block;
            margin: 8px; /* 10px'in %75'i */
            font-weight: 600;
            transition: transform 0.3s;
            cursor: pointer;
            font-family: 'Kalam', cursive;
            font-size: 0.75rem; /* 1rem'in %75'i */
        }

        .btn:hover {
            transform: translateY(-1.5px); /* -2px'in %75'i */
        }

        .btn-success {
            background: linear-gradient(135deg, #56ab2f 0%, #a8e6cf 100%);
        }

        .btn-info {
            background: linear-gradient(135deg, #3498db 0%, #85c1e9 100%);
        }

        .btn-warning {
            background: linear-gradient(135deg, #f39c12 0%, #f7dc6f 100%);
        }

        .btn-danger {
            background: linear-gradient(135deg, #e74c3c 0%, #f1948a 100%);
        }

        .btn-small {
            padding: 6px 11px; /* 8px 15px'in %75'i */
            font-size: 0.68rem; /* 0.9rem'in %75'i */
            margin: 4px; /* 5px'in %75'i */
        }

        .uploaders-container {
            background: white;
            padding: 19px; /* 25px'in %75'i */
            border-radius: 11px; /* 15px'in %75'i */
            box-shadow: 0 4px 11px rgba(0,0,0,0.08); /* %75 oranında */
        }

        .uploader-folder {
            margin-bottom: 15px; /* 20px'in %75'i */
            border: 1px solid #e0e0e0;
            border-radius: 8px; /* 10px'in %75'i */
            overflow: hidden;
        }

        .folder-header {
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 15px; /* 20px'in %75'i */
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.3s;
        }

        .folder-header:hover {
            background: linear-gradient(135deg, #e9ecef 0%, #dee2e6 100%);
        }

        .folder-header.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }

        .folder-info {
            display: flex;
            align-items: center;
            gap: 11px; /* 15px'in %75'i */
        }

        .folder-icon {
            font-size: 1.12rem; /* 1.5rem'in %75'i */
            transition: transform 0.3s;
        }

        .folder-header.active .folder-icon {
            transform: rotate(90deg);
        }

        .folder-details {
            font-size: 0.71rem; /* 0.95rem'in %75'i */
            opacity: 0.8;
            font-family: 'Caveat', cursive;
        }

        .folder-actions {
            display: flex;
            gap: 8px; /* 10px'in %75'i */
        }

        .folder-content {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out;
        }

        .folder-content.expanded {
            max-height: 750px; /* 1000px'in %75'i */
        }

        .photos-table {
            width: 100%;
            border-collapse: collapse;
        }

        .photos-table th,
        .photos-table td {
            padding: 9px; /* 12px'in %75'i */
            text-align: left;
            border-bottom: 1px solid #eee;
            font-family: 'Caveat', cursive;
            font-size: 0.75rem; /* 1rem'in %75'i */
        }

        .photos-table th {
            background: #f8f9fa;
            font-weight: 600;
            color: #333;
            font-family: 'Kalam', cursive;
            font-size: 0.82rem; /* 1.1rem'in %75'i */
        }

        .photos-table tr:hover {
            background: #f8f9fa;
        }

        h1, h2, h3 {
            margin-bottom: 15px; /* 20px'in %75'i */
            font-family: 'Dancing Script', cursive;
        }

        h3 {
            font-size: 1.35rem; /* 1.8rem'in %75'i */
            color: #333;
        }

        .empty-state {
            text-align: center;
            padding: 38px; /* 50px'in %75'i */
            color: #666;
            font-family: 'Caveat', cursive;
        }

        .empty-state-icon {
            font-size: 3rem; /* 4rem'in %75'i */
            margin-bottom: 15px; /* 20px'in %75'i */
        }

        .empty-state h3 {
            font-size: 1.12rem; /* 1.5rem'in %75'i */
            margin-bottom: 11px; /* 15px'in %75'i */
        }

        .empty-state p {
            font-size: 0.82rem; /* 1.1rem'in %75'i */
        }

        .expand-all-btn {
            margin-bottom: 15px; /* 20px'in %75'i */
        }

        .folder-stats {
            display: flex;
            gap: 15px; /* 20px'in %75'i */
            font-size: 0.71rem; /* 0.95rem'in %75'i */
            font-family: 'Caveat', cursive;
        }

        .folder-stat {
            display: flex;
            align-items: center;
            gap: 4px; /* 5px'in %75'i */
        }

        .alert {
            padding: 11px; /* 15px'in %75'i */
            border-radius: 8px; /* 10px'in %75'i */
            margin-bottom: 15px; /* 20px'in %75'i */
            font-family: 'Kalam', cursive;
            font-size: 0.75rem; /* 1rem'in %75'i */
            font-weight: 500;
        }

        .alert-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }

        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }

        .current-bg-preview {
            margin-top: 8px; /* 10px'in %75'i */
            text-align: center;
        }

        .current-bg-preview p {
            font-family: 'Caveat', cursive;
            font-size: 0.75rem; /* 1rem'in %75'i */
            margin-bottom: 8px; /* 10px'in %75'i */
        }

        .current-bg-preview img {
            max-width: 150px; /* 200px'in %75'i */
            max-height: 75px; /* 100px'in %75'i */
            border-radius: 6px; /* 8px'in %75'i */
            border: 2px solid #e0e0e0;
        }

        small {
            font-family: 'Caveat', cursive;
            font-size: 0.68rem; /* 0.9rem'in %75'i */
        }

        strong {
            font-family: 'Kalam', cursive;
            font-weight: 600;
        }

        @media (max-width: 768px) {
            body {
                font-size: 11px; /* 15px'in %75'i */
            }

            .header h1 {
                font-size: 1.5rem; /* 2rem'in %75'i */
            }

            .header h2 {
                font-size: 0.98rem; /* 1.3rem'in %75'i */
            }

            .folder-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 11px; /* 15px'in %75'i */
            }

            .folder-actions {
                width: 100%;
                justify-content: flex-start;
            }

            .photos-table {
                font-size: 0.64rem; /* 0.85rem'in %75'i */
            }

            .photos-table th,
            .photos-table td {
                padding: 6px; /* 8px'in %75'i */
            }

            .folder-stats {
                flex-direction: column;
                gap: 8px; /* 10px'in %75'i */
            }

            .settings-form {
                grid-template-columns: 1fr;
            }

            .stat-number {
                font-size: 1.35rem; /* 1.8rem'in %75'i */
            }

            .stat-label {
                font-size: 0.75rem; /* 1rem'in %75'i */
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎭 {{ settings.site_title }}</h1>
            <h2>Admin Panel - Düğün Fotoğrafları</h2>
        </div>

        <!-- Flash Messages -->
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ 'success' if category == 'success' else 'error' }}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <!-- Site Ayarları Bölümü -->
        <div class="settings-section">
            <h3>⚙️ Site Ayarları</h3>
            <form method="POST" action="{{ url_for('update_settings') }}" class="settings-form" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="site_title">📝 Site Başlığı</label>
                    <input type="text" id="site_title" name="site_title" 
                           value="{{ settings.site_title }}" 
                           placeholder="Örn: BYT DIGITAL" required>
                </div>
                <div class="form-group">
                    <label for="site_description">📄 Site Açıklaması (Çok satırlı yazabilirsiniz)</label>
                    <textarea id="site_description" name="site_description" 
                              rows="3" 
                              placeholder="Örn: Anılarınızı Paylaşın&#10;Sevgimizin Tanıkları Olun&#10;Bu Güzel Günde Bizimle Olun">{{ settings.site_description }}</textarea>
                    <small style="color: #666; font-size: 0.68rem;">💡 İpucu: Enter tuşuyla yeni satıra geçebilirsiniz</small>
                </div>
                <div class="form-group">
                    <label for="site_emoji">😊 Site Emojisi (Birden fazla emoji ekleyebilirsiniz)</label>
                    <input type="text" id="site_emoji" name="site_emoji" 
                           class="emoji-input"
                           value="{{ settings.site_emoji }}" 
                           placeholder="Örn: 💒❤️💍 veya 🎉🥳💕" maxlength="50">
                    <small style="color: #666; font-size: 0.68rem;">💡 İpucu: Emojileri yan yana yazabilirsiniz</small>
                </div>
                <div class="form-group">
                    <label for="background_image">🌄 Sayfa Arka Plan Resmi</label>
                    <input type="file" id="background_image" name="background_image" 
                           accept="image/*">
                    <small style="color: #666; font-size: 0.68rem;">💡 Sayfanın genel arka planında görünecek resmi yükleyin</small>
                    {% if settings.background_image %}
                        <div class="current-bg-preview">
                            <p>Mevcut sayfa arka planı:</p>
                            <img src="/static/{{ settings.background_image }}" alt="Mevcut arka plan">
                            <br><br>
                            <a href="{{ url_for('delete_background') }}" class="btn btn-danger btn-small">
                                🗑️ Sayfa Arka Planını Sil
                            </a>
                        </div>
                    {% endif %}
                </div>
                <div class="form-group">
                    <label for="background_opacity">🔍 Sayfa Arka Plan Şeffaflığı</label>
                    <input type="range" id="background_opacity" name="background_opacity" 
                           min="0" max="1" step="0.1" 
                           value="{{ settings.background_opacity }}"
                           oninput="document.getElementById('opacity-value').textContent = this.value">
                    <small style="color: #666; font-size: 0.68rem;">Şeffaflık: <span id="opacity-value">{{ settings.background_opacity }}</span></small>
                </div>
                <div class="form-group">
                    <label for="container_background_image">🖼️ Modal (Container) Arka Plan Resmi</label>
                    <input type="file" id="container_background_image" name="container_background_image" 
                           accept="image/*">
                    <small style="color: #666; font-size: 0.68rem;">💡 Yüzen modal kutusunun arka planında görünecek resmi yükleyin</small>
                    {% if settings.container_background_image %}
                        <div class="current-bg-preview">
                            <p>Mevcut modal arka planı:</p>
                            <img src="/static/{{ settings.container_background_image }}" alt="Mevcut modal arka plan">
                            <br><br>
                            <a href="{{ url_for('delete_container_background') }}" class="btn btn-danger btn-small">
                                🗑️ Modal Arka Planını Sil
                            </a>
                        </div>
                    {% endif %}
                </div>
                <div class="form-group">
                    <label for="container_background_opacity">🎭 Modal Arka Plan Şeffaflığı</label>
                    <input type="range" id="container_background_opacity" name="container_background_opacity" 
                           min="0" max="1" step="0.1" 
                           value="{{ settings.container_background_opacity }}"
                           oninput="document.getElementById('container-opacity-value').textContent = this.value">
                    <small style="color: #666; font-size: 0.68rem;">Şeffaflık: <span id="container-opacity-value">{{ settings.container_background_opacity }}</span></small>
                </div>
                <div class="form-group">
                    <label for="footer_text">📝 Footer Metni</label>
                    <input type="text" id="footer_text" name="footer_text" 
                           value="{{ settings.footer_text }}" 
                           placeholder="Örn: Sevgili {{ site_title }} Ailesi">
                    <small style="color: #666; font-size: 0.68rem;">💡 İpucu: {site_title} yazdığınız yerde site başlığı görünecek</small>
                </div>
                <div class="form-group">
                    <label for="copyright_text">©️ Telif Hakkı Metni</label>
                    <input type="text" id="copyright_text" name="copyright_text" 
                           value="{{ settings.copyright_text }}" 
                           placeholder="Örn: BYT DIGITAL © 2025 - TÜM HAKLARI SAKLIDIR">
                    <small style="color: #666; font-size: 0.68rem;">💼 Kurumsal telif hakkı metni</small>
                </div>
                <div class="form-group">
                    <button type="submit" class="btn">
                        💾 Ayarları Kaydet
                    </button>
                </div>
            </form>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number">{{ total_photos }}</div>
                <div class="stat-label">📸 Toplam Fotoğraf</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ total_size_mb }} MB</div>
                <div class="stat-label">💾 Toplam Boyut</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ total_uploaders }}</div>
                <div class="stat-label">👥 Yükleyici Sayısı</div>
            </div>
        </div>

        <div class="actions">
            <h3>📋 İşlemler</h3>
            {% if total_photos > 0 %}
                <a href="{{ url_for('download_all') }}" class="btn btn-success">
                    📦 Tüm Fotoğrafları ZIP İndir
                </a>
            {% endif %}
            <a href="{{ url_for('generate_qr') }}" class="btn btn-info">
                📱 QR Kod İndir
            </a>
            <a href="{{ url_for('upload_form') }}" class="btn">
                🏠 Ana Sayfaya Dön
            </a>
        </div>

        <div class="uploaders-container">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h3>👥 Yükleyenlere Göre Fotoğraflar</h3>
                <div class="expand-all-btn">
                    <button class="btn btn-small" onclick="toggleAllFolders()">
                        📁 Tümünü Aç/Kapat
                    </button>
                </div>
            </div>

            {% if grouped_photos %}
                {% for uploader, photos in grouped_photos.items() %}
                    {% set stats = uploader_stats[uploader] %}
                    <div class="uploader-folder">
                        <div class="folder-header" onclick="toggleFolder('{{ uploader|replace("'", "\\'") }}')">
                            <div class="folder-info">
                                <span class="folder-icon">📁</span>
                                <div>
                                    <strong>👤 {{ uploader }}</strong>
                                    <div class="folder-stats">
                                        <div class="folder-stat">
                                            <span>📸</span>
                                            <span>{{ stats.count }} fotoğraf</span>
                                        </div>
                                        <div class="folder-stat">
                                            <span>💾</span>
                                            <span>{{ "%.1f"|format(stats.size / 1024 / 1024) }} MB</span>
                                        </div>
                                        <div class="folder-stat">
                                            <span>🕒</span>
                                            <span>{{ stats.latest[:19] if stats.latest else 'Bilinmiyor' }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="folder-actions" onclick="event.stopPropagation()">
                                <a href="{{ url_for('download_uploader_photos', uploader_name=uploader) }}" 
                                   class="btn btn-warning btn-small">
                                    📥 İndir
                                </a>
                            </div>
                        </div>
                        <div class="folder-content" id="folder-{{ uploader|replace(' ', '_')|replace('/', '_') }}">
                            <table class="photos-table">
                                <thead>
                                    <tr>
                                        <th>📁 Dosya Adı</th>
                                        <th>📅 Yükleme Tarihi</th>
                                        <th>💾 Boyut</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for photo in photos %}
                                    <tr>
                                        <td>{{ photo.original_filename }}</td>
                                        <td>{{ photo.upload_time }}</td>
                                        <td>{{ "%.2f"|format(photo.file_size / 1024 / 1024) }} MB</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📷</div>
                    <h3>Henüz fotoğraf yüklenmemiş</h3>
                    <p>QR kodu paylaşarak misafirlerin fotoğraf yüklemesini sağlayın.</p>
                </div>
            {% endif %}
        </div>
    </div>

    <script>
        function toggleFolder(uploaderName) {
            const safeName = uploaderName.replace(/\s/g, '_').replace(/\//g, '_');
            const content = document.getElementById('folder-' + safeName);
            const header = content.previousElementSibling;

            if (content.classList.contains('expanded')) {
                content.classList.remove('expanded');
                header.classList.remove('active');
            } else {
                content.classList.add('expanded');
                header.classList.add('active');
            }
        }

        function toggleAllFolders() {
            const allContents = document.querySelectorAll('.folder-content');
            const allHeaders = document.querySelectorAll('.folder-header');

            // Check if any folder is open
            const hasExpanded = Array.from(allContents).some(content => 
                content.classList.contains('expanded')
            );

            // If any is open, close all. Otherwise, open all.
            allContents.forEach((content, index) => {
                const header = allHeaders[index];
                if (hasExpanded) {
                    content.classList.remove('expanded');
                    header.classList.remove('active');
                } else {
                    content.classList.add('expanded');
                    header.classList.add('active');
                }
            });
        }

        // Auto-close folders when clicking outside
        document.addEventListener('click', function(event) {
            if (!event.target.closest('.uploader-folder')) {
                // Optional: Close all folders when clicking outside
                // Uncomment the next lines if you want this behavior
                /*
                document.querySelectorAll('.folder-content.expanded').forEach(content => {
                    content.classList.remove('expanded');
                    content.previousElementSibling.classList.remove('active');
                });
                */
            }
        });
    </script>
</body>
</html>'''

    with open('templates/upload.html', 'w', encoding='utf-8') as f:
        f.write(upload_html)

    with open('templates/admin.html', 'w', encoding='utf-8') as f:
        f.write(admin_html)


if __name__ == '__main__':
    # Veritabanını başlat
    init_db()

    # Template'leri oluştur
    create_templates()

    print("🎉 BYT DIGITAL Düğün Fotoğraf Uygulaması Başlatılıyor...")
    print("📱 Ana sayfa: http://localhost:5000")
    print("🔧 Admin panel: http://localhost:5000/admin")
    print("📱 QR kod oluştur: http://localhost:5000/qr")

    # Flask uygulamasını başlat
    app.run(debug=True, host='0.0.0.0', port=5000)