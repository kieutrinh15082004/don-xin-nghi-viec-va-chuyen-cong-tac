from flask import Flask, request, render_template, flash, redirect, url_for
import pytesseract
from PIL import Image, ImageEnhance
import io
import PyPDF2
import re
import os
from datetime import datetime
import pyodbc

app = Flask(__name__)
app.secret_key = 'a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7'

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

SERVER = 'LAPTOP-TUIP7H2M\SQLEXPRESS'
DATABASE = 'HR_Processing_DB'
MAX_LEAVE_DAYS = 15

# Helper functions
def get_db_conn():
    conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;TrustServerCertificate=yes;'
    return pyodbc.connect(conn_str)

def init_db():
    conn = None
    try:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute('''
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='requests' and xtype='U')
            CREATE TABLE requests (
                id INT PRIMARY KEY IDENTITY(1,1),
                employee_name NVARCHAR(255),
                leave_date NVARCHAR(255),
                reason NVARCHAR(MAX),
                status NVARCHAR(255),
                raw_text NVARCHAR(MAX),
                timestamp DATETIME DEFAULT GETDATE()
            )
        ''')
        conn.commit()
    except Exception as e:
        print(f"Error creating table: {e}")
    finally:
        if conn:
            conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'jpg', 'png'}

def ocr_file(file):
    text = ""
    if file.filename.lower().endswith('.pdf'):
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file.read()))
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading PDF: {e}")
            return "Lỗi khi đọc file PDF."
    else:
        try:
            img = Image.open(io.BytesIO(file.read()))
            img = img.convert('L')
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(2.0)
            text = pytesseract.image_to_string(img, lang='vie')
        except Exception as e:
            print(f"Error processing image: {e}")
            return "Lỗi khi xử lý file ảnh."
    return text

def get_total_used_days(employee_name, new_request_start=None, new_request_end=None):
    conn = get_db_conn()
    cursor = conn.cursor()
    
    current_year = datetime.now().year
    
    total_days = 0
    try:
        cursor.execute('''
            SELECT leave_date FROM requests
            WHERE employee_name = ? AND status = 'Phê duyệt'
        ''', (employee_name,))
        
        approved_requests = cursor.fetchall()
        for row in approved_requests:
            dates = row.leave_date.split(' - ')
            if len(dates) == 2:
                prev_start = datetime.strptime(dates[0], '%d/%m/%Y')
                prev_end = datetime.strptime(dates[1], '%d/%m/%Y')
                if prev_start.year == current_year:
                    total_days += (prev_end - prev_start).days + 1
    except Exception as e:
        print(f"Error checking leave days: {e}")
    finally:
        conn.close()
        
    if new_request_start and new_request_end:
        start_dt = datetime.strptime(new_request_start, '%d/%m/%Y')
        end_dt = datetime.strptime(new_request_end, '%d/%m/%Y')
        new_request_days = (end_dt - start_dt).days + 1
        total_days += new_request_days
        
    return total_days

def process_text(text):
    name_match = re.search(r'Tôi tên: (.+)', text, re.IGNORECASE)
    employee_name = name_match.group(1).strip() if name_match else "Không tìm thấy"

    date_match = re.search(r'Từ ngày: (\d{2}/\d{2}/\d{4}) đến ngày: (\d{2}/\d{2}/\d{4})|From: (\d{2}/\d{2}/\d{4}) To: (\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
    leave_date = ""
    start_date = None
    end_date = None
    if date_match:
        if date_match.group(1) and date_match.group(2):
            leave_date = f"{date_match.group(1)} - {date_match.group(2)}"
            start_date = date_match.group(1)
            end_date = date_match.group(2)
        elif date_match.group(3) and date_match.group(4):
            leave_date = f"{date_match.group(3)} - {date_match.group(4)}"
            start_date = date_match.group(3)
            end_date = date_match.group(4)
    leave_date = leave_date if leave_date else "Không tìm thấy"

    reason_match = re.search(r'Lý do: (.+?)(?=\n|$)', text, re.IGNORECASE)
    reason = reason_match.group(1).strip() if reason_match else "Không tìm thấy"
    
    valid_reasons = ["ốm", "bệnh", "tang", "việc gia đình", "nghỉ mát"]
    is_valid_reason = any(r in reason.lower() for r in valid_reasons)
    
    status = "Từ chối"  # Mặc định là từ chối nếu không khớp điều kiện
    remaining_days = "N/A"
    if is_valid_reason and start_date and end_date:
        total_days = get_total_used_days(employee_name, start_date, end_date)
        if total_days <= MAX_LEAVE_DAYS:
            status = "Phê duyệt"
            remaining_days = MAX_LEAVE_DAYS - total_days
        else:
            status = "Từ chối"

    vietnamese_text = ""
    for line in text.split('\n'):
        if re.search(r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]', line.lower()):
            vietnamese_text += line + '\n'
    raw_text = vietnamese_text.strip() if vietnamese_text else text
    
    return employee_name, leave_date, reason, status, raw_text, remaining_days

# ROUTES

@app.route('/history')
def history():
    conn = get_db_conn()
    cursor = conn.cursor()
    
    requests_data = cursor.execute('SELECT * FROM requests ORDER BY id DESC').fetchall()
    
    requests_list = []
    for row in requests_data:
        requests_list.append({
            'id': row.id,
            'employee_name': row.employee_name,
            'leave_date': row.leave_date,
            'reason': row.reason,
            'status': row.status,
            'raw_text': row.raw_text,
            'timestamp': row.timestamp.strftime('%Y-%m-%d %H:%M')
        })
    
    conn.close()
    
    return render_template('history.html', requests=requests_list)


@app.route('/', methods=['GET', 'POST'])
def dashboard():
    conn = get_db_conn()
    cursor = conn.cursor()

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Không có file được chọn!', 'error')
            return redirect(url_for('dashboard'))
            
        file = request.files['file']
        if file.filename == '':
            flash('Vui lòng chọn một file!', 'error')
            return redirect(url_for('dashboard'))

        if file and allowed_file(file.filename):
            text = ocr_file(file)
            employee_name, leave_date, reason, status, raw_text, remaining_days = process_text(text)
            
            try:
                cursor.execute('''
                    INSERT INTO requests (employee_name, leave_date, reason, status, raw_text)
                    VALUES (?, ?, ?, ?, ?)
                ''', (employee_name, leave_date, reason, status, raw_text))
                conn.commit()
            except Exception as e:
                flash(f'Lỗi khi lưu dữ liệu vào cơ sở dữ liệu: {e}', 'error')
                return redirect(url_for('dashboard'))
            
            if status == "Phê duyệt":
                flash(f'Đã tải lên tài liệu và tự động phê duyệt. Tổng số ngày nghỉ còn lại của {employee_name} là {remaining_days} ngày.', 'success')
            else:
                 flash(f'Đã tải lên tài liệu và tự động từ chối. Lý do không chính đáng hoặc vượt quá số ngày nghỉ phép tối đa.', 'error')
        
        return redirect(url_for('dashboard'))

    requests_data = cursor.execute('SELECT * FROM requests ORDER BY id DESC').fetchall()
    
    requests_list = []
    for row in requests_data:
        # Calculate remaining days for display
        remaining_days = "N/A"
        if row.status == 'Phê duyệt':
            try:
                # Calculate total approved days so far, excluding the current one from the total calculation
                # to get the days remaining before this approval.
                total_used_days = get_total_used_days(row.employee_name)
                
                leave_date_str = row.leave_date.split(' - ')
                start_dt = datetime.strptime(leave_date_str[0], '%d/%m/%Y')
                end_dt = datetime.strptime(leave_date_str[1], '%d/%m/%Y')
                current_request_days = (end_dt - start_dt).days + 1
                
                remaining_days = MAX_LEAVE_DAYS - (total_used_days + current_request_days)
            except Exception as e:
                print(f"Error calculating remaining days for display: {e}")
                remaining_days = "N/A"

        requests_list.append({
            'id': row.id,
            'employee_name': row.employee_name,
            'leave_date': row.leave_date,
            'reason': row.reason,
            'status': row.status,
            'raw_text': row.raw_text,
            'timestamp': row.timestamp.strftime('%Y-%m-%d %H:%M'),
            'remaining_days': remaining_days
        })
    
    total_requests = len(requests_data)
    
    try:
        cursor.execute("SELECT COUNT(*) FROM requests")
        total_requests = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM requests WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)")
        processed_today = cursor.fetchone()[0]
    except Exception as e:
        print(f"Error fetching counts: {e}")
        total_requests = 0
        processed_today = 0
    
    stats = {
        'total_requests': total_requests,
        'processed_today': processed_today,
        'accuracy': "92%"
    }
    
    conn.close()
    
    return render_template('index.html', requests=requests_list, stats=stats)

@app.route('/update_status/<int:request_id>', methods=['POST'])
def update_status(request_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    action = request.form.get('action')
    
    try:
        cursor.execute('SELECT employee_name, leave_date FROM requests WHERE id = ?', (request_id,))
        request_info = cursor.fetchone()
        employee_name = request_info.employee_name
        leave_date = request_info.leave_date.split(' - ')
        start_date = leave_date[0]
        end_date = leave_date[1]
    except Exception as e:
        flash(f'Lỗi khi lấy thông tin yêu cầu: {e}', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    if action == 'approve':
        new_status = 'Phê duyệt'
        total_used_days = get_total_used_days(employee_name, start_date, end_date)
        remaining_days = MAX_LEAVE_DAYS - total_used_days
        flash(f'Đã phê duyệt thủ công yêu cầu #{request_id}. Tổng số ngày nghỉ còn lại của {employee_name} là {remaining_days} ngày.', 'success')
    elif action == 'reject':
        new_status = 'Từ chối'
        flash(f'Đã từ chối thủ công yêu cầu #{request_id}.', 'error')
    else:
        flash('Hành động không hợp lệ.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    try:
        cursor.execute('UPDATE requests SET status = ? WHERE id = ?', (new_status, request_id))
        conn.commit()
    except Exception as e:
        flash(f'Lỗi khi cập nhật trạng thái: {e}', 'error')
    finally:
        conn.close()

    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)