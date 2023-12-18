from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
# from celery import Celery
import os
import sys
import tqdm
import uuid

# Threading for job progress
import threading
thread_local = threading.local()

# ignore the ./uploads folder
app = Flask(__name__)
CORS(app)

# Configuration
STORAGE_FOLDER = './storage'
ALLOWED_EXTENSIONS = {'mp3', 'mp4', 'wav', 'mov'}

app.config['STORAGE_FOLDER'] = STORAGE_FOLDER

app.config['SECRET_KEY'] = 'your_secret_key'
# /Users/erikbahena/Desktop/steno/sqlite.db
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////Users/erikbahena/Desktop/steno/storage/sqlite.db'

app.config['JWT_SECRET_KEY'] = 'your_jwt_secret'
# app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
# app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

db = SQLAlchemy(app)
jwt = JWTManager(app)
limiter = Limiter(app=app, key_func=get_remote_address, enabled=False)
socketio = SocketIO(app, cors_allowed_origins="*")
# celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
# celery.conf.update(app.config)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)

class Transcription(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    result = db.Column(db.Text, nullable=True)
    vtt_path = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class _CustomProgressBar(tqdm.tqdm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = self.n  # Set the initial value
        
    def update(self, n):
        super().update(n)
        self._current += n

        # Percent
        percent = ((self._current / self.total) * 100).__round__(2)
        job_id = getattr(thread_local, "job_id", None)
        if job_id:
            socketio.emit('transcription_update', {'job_id': job_id, 'progress': percent})

import whisper.transcribe 
transcribe_module = sys.modules['whisper.transcribe']
transcribe_module.tqdm.tqdm = _CustomProgressBar

import whisper
from whisper.utils import get_writer
model = whisper.load_model("/Users/erikbahena/Desktop/base.pt") # or use use the default model load_model("base")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_timestamp(time):
    seconds = int(time)
    milliseconds = int((time - seconds) * 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

def transcribe(file_path):
    """Transcribes the given audio data."""    
    try:
        result = whisper.transcribe(model, file_path, word_timestamps=True, fp16=False, suppress_blank=True, verbose=None)
        return result
    except Exception as e:
        print(f"Error during transcription: {e}")
        return False
    
@app.route('/register', methods=['POST'], endpoint='register')
def register():
    data = request.get_json()

    # Check if user already exists
    existing_user = User.query.filter_by(username=data['username']).first()
    if existing_user:
        return jsonify({"error": "Username already taken."}), 400  # 400 Bad Request

    # Proceed with registration
    hashed_password = generate_password_hash(data['password'])
    new_user = User(username=data['username'], password=hashed_password)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        
        # return an access token
        access_token = create_access_token(identity=new_user.username)
        return jsonify(access_token=access_token)
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Registration failed. Please try again later."}), 500  # 500 Internal Server Error


@app.route('/login', methods=['POST'], endpoint='login')
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data['username']).first()
    if not user or not check_password_hash(user.password, data['password']):
        return jsonify({"message": "Invalid credentials."}), 401
    access_token = create_access_token(identity=user.username)
    return jsonify(access_token=access_token)

# Saving this for a later time
# @celery.task
# def background_transcription(job_id, file_path):
#     with app.app_context():
#         try:
#             # Start the transcription process
#             result = transcribe(file_path)

#             if not result:
#                 raise Exception("Transcription failed.")

#             # Create a Transcription record
#             transcription_record = Transcription(id=job_id, progress=100.0, result=result)
#             db.session.add(transcription_record)
#             db.session.commit()

#             socketio.emit('transcription_update', {'job_id': job_id, 'progress': 100}, namespace='/transcription')

#         except Exception as e:
#             # Log and handle the error, you might also want to update the Transcription record to indicate an error
#             print(f"Error during background transcription: {e}")

#             # Optionally, send real-time updates via Flask-SocketIO
#             socketio.emit('transcription_error', {'job_id': job_id, 'error': str(e)}, namespace='/transcription')

    
@app.route('/start_transcription', methods=['POST'], endpoint='start_transcription')
@limiter.limit("5 per minute")
@jwt_required()
def start_transcription():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        try:
            job_id = str(uuid.uuid4())
            user_name = get_jwt_identity()  # Get the JWT identity
            user_id = User.query.filter_by(username=user_name).first().id
            thread_local.job_id = job_id

            # emit progress to client
            socketio.emit('transcription_update', {'job_id': job_id, 'progress': 1})

            # Create a Transcription record
            transcription_record = Transcription(id=job_id, user_id=user_id)
            db.session.add(transcription_record)
            db.session.commit()

            # Save the file to storage
            filename = secure_filename(file.filename)
            dir_path = os.path.join(app.config['STORAGE_FOLDER'], job_id)
            os.mkdir(dir_path)
            file_path = os.path.join(app.config['STORAGE_FOLDER'], job_id, filename)
            file.save(file_path)
            print(f"Saved file to {file_path}")


            # Run the transcription on the file
            whisper_output = transcribe(file_path)

            # Save the result using writer
            output_dir = os.path.join(app.config['STORAGE_FOLDER'], job_id)
            writer = get_writer("vtt", output_dir)  # Or any other format you desire
            writer(whisper_output, 'output.vtt', options={
                "max_line_width": 10,
                "max_line_count": 1,
                "highlight_words": False
            })

            # Emit this file contents to the client in transcription_result
            with open(os.path.join(output_dir, 'output.vtt'), 'r') as f:
                result = f.read()
                # Update the Transcription record
                transcription_record.result = result
                transcription_record.vtt_path = os.path.join(output_dir, 'output.vtt')
                db.session.commit()

                socketio.emit('transcription_result', {'job_id': job_id, 'result': result, "segments": whisper_output["segments"]})

            return jsonify({'job_id': job_id})
        except Exception as e:
            db.session.rollback()
            print(f"Error during transcription: {e}")
            return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/get_transcription/<job_id>', methods=['GET'], endpoint='get_transcription')
@jwt_required()
def get_transcription(job_id):
    transcription = Transcription.query.filter_by(id=job_id).first()
    if not transcription:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"result": transcription.result, "progress": transcription.progress})

@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Create the database tables
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)