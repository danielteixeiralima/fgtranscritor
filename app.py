import random
from dotenv import load_dotenv
load_dotenv()

import os
import io
import logging
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from openai_service import analyze_meeting, transcribe_audio, generate_meeting_agenda
from models import db, User, Meeting
from templates_data import WEB_SUMMIT_AGENDA, CAKE_RECIPE_AGENDA, HOURLY_COST
from gmail_service import gmail_service
import requests
from flask_migrate import Migrate
import click
from datetime import datetime
import dateutil.parser
from flask import Flask
from markupsafe import Markup
import pytz 







# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 60,
    "connect_args": {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
}

@app.template_filter('datetime')
def format_datetime(value, fmt='%d/%m/%Y %H:%M'):
    """Converte ISO string em datetime formatado."""
    # parseia strings '2025-05-28T15:00:00-03:00'
    dt = dateutil.parser.isoparse(value)
    return dt.strftime(fmt)

@app.template_filter('nl2br')
def nl2br_filter(s):
    """Converte quebras de linha em <br> e marca como seguro para HTML."""
    if s is None:
        return ''
    # Substitui '\n' por '<br>\n' e retorna como Markup para não escapar as tags
    return Markup(s.replace('\n', '<br>\n'))



@app.template_filter('to_brt')
def to_brt(value):
    """
    Converte um datetime aware (ou naive) para fuso 'America/Sao_Paulo' (UTC–3),
    retornando um novo datetime com tzinfo adequado.
    """
    if value is None:
        return None

    # Se vier como string, você pode tentar parsear, mas aqui esperamos um datetime
    # Se o datetime estiver “naive”, assumimos UTC antes de converter:
    if value.tzinfo is None:
        # assume que value está em UTC se vier “naive”
        value = value.replace(tzinfo=pytz.utc)

    brt_tz = pytz.timezone('America/Sao_Paulo')
    return value.astimezone(brt_tz)

# Initialize database
db.init_app(app)
migrate = Migrate(app, db)
# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
app.debug = True
# Envia logs DEBUG também para o stdout
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)



@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception as e:
        logger.error(f"Error loading user: {str(e)}")
        return None

# Create tables if they don't exist
# with app.app_context():
#     db.create_all()
#     logger.debug("Database tables created or confirmed")
#     if not User.query.filter_by(username='admin').first():
#         admin = User(
#             username='admin',
#             email='admin@bizarte.com.br',  # ajuste se quiser outro email
#             admin=True
#         )
#         admin.set_password('admin123')
#         db.session.add(admin)
#         db.session.commit()
#         logger.info("Usuário admin criado: admin / admin123")

@app.route('/')
def index():
    """Render the home page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handle user registration"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        # Basic validation
        if not all([username, email, password, password_confirm]):
            flash('Todos os campos são obrigatórios!', 'danger')
            return redirect(url_for('register'))
        
        if password != password_confirm:
            flash('As senhas não coincidem!', 'danger')
            return redirect(url_for('register'))
        
        # Check if user already exists
        if User.query.filter_by(username=username).first():
            flash('Nome de usuário já existe!', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('E-mail já está registrado!', 'danger')
            return redirect(url_for('register'))
        
        # Create new user
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Enviar email de verificação
        def generate_verification_code():
            return "{:06d}".format(random.randint(0, 999999))

        from gmail_service import gmail_service
        verification_code = generate_verification_code()
        user.verification_code = verification_code
        user.verification_code_sent_at = datetime.utcnow()
        db.session.commit()
        gmail_service.send_verification_email(user.email, user.username, verification_code)

        flash('Registro realizado com sucesso! Verifique seu email para ativar a conta.', 'info')
        return render_template('verify_email.html', user=user)
    
    return render_template('register.html')


@app.route('/verify-email', methods=['GET', 'POST'])
def verify_email():
    """Handle email verification"""
    # Check if user is in verification process
    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('Sessão de verificação expirou. Faça login novamente.', 'warning')
        return redirect(url_for('login'))
    
    user = User.query.get(user_id)
    if not user:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('login'))
    
    # If user is already verified, redirect to dashboard
    if user.email_verified:
        session.pop('pending_verification_user_id', None)
        login_user(user)
        flash('Email já verificado! Bem-vindo!', 'success')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        token = request.form.get('verification_code')

        if not token:
            flash('Por favor, insira o código de verificação.', 'danger')
            return redirect(url_for('verify_email'))

        # Verifica se o código bate com o salvo no banco
        if user.verification_code and token == user.verification_code:
            user.email_verified = True
            user.verification_code = None
            db.session.commit()
            session.pop('pending_verification_user_id', None)
            login_user(user)
            flash('Email verificado com sucesso! Bem-vindo!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Código de verificação inválido ou expirado.', 'danger')
            return redirect(url_for('verify_email'))

    return render_template('verify_email.html', user=user)


@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    """Reenvia o código de verificação por email usando gmail_service"""
    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('Sessão de verificação expirou.', 'warning')
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('login'))

    # Gerar novo código e atualizar timestamp
    verification_code = "{:06d}".format(random.randint(0, 999999))
    user.verification_code = verification_code
    user.verification_code_sent_at = datetime.utcnow()
    db.session.commit()

    # Enviar email
    success, message = gmail_service.send_verification_email(user.email, user.username, verification_code)
    if success:
        flash('Novo código enviado para seu email.', 'success')
    else:
        flash(f'Erro ao enviar email: {message}', 'danger')

    return redirect(url_for('verify_email'))



@app.route('/users')
@login_required
def list_users():
    if not current_user.is_admin:
        flash('Permissão negada.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html', users=users)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form

        if not username or not password:
            flash('Por favor, insira nome de usuário e senha!', 'danger')
            return redirect(url_for('login'))

        # Find user by username
        user = User.query.filter_by(username=username).first()

        # Check if user exists and password is correct
        if user and user.check_password(password):
            # Check if email is verified
            if not user.email_verified:
                flash('Você precisa verificar seu email antes de fazer login!', 'warning')
                session['pending_verification_user_id'] = user.id
                return redirect(url_for('verify_email'))
            
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            flash(f'Bem-vindo, {user.username}!', 'success')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Nome de usuário ou senha incorretos!', 'danger')
            return redirect(url_for('login'))
    
    return render_template('login.html')



@app.route('/logout')
@login_required
def logout():
    """Handle user logout"""
    logout_user()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Show user dashboard with statistics and recent meetings"""
    # Get user's meetings, most recent first (limited to 5)
    recent_meetings = Meeting.query.filter_by(user_id=current_user.id).order_by(Meeting.created_at.desc()).limit(5).all()
    
    # Get meeting statistics
    try:
        stats = {}
        # Total number of meetings
        stats['total_meetings'] = Meeting.query.filter_by(user_id=current_user.id).count()
        # Average alignment score
        avg_score_result = db.session.query(db.func.avg(Meeting.alignment_score)).filter(Meeting.user_id == current_user.id).first()
        stats['avg_alignment_score'] = round(avg_score_result[0] or 0, 2) if avg_score_result[0] is not None else 0
        # Meetings this month
        first_day = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        stats['meetings_this_month'] = Meeting.query.filter(
            Meeting.user_id == current_user.id,
            Meeting.created_at >= first_day
        ).count()
        # Get language distribution, filtrando 'auto'
        languages = db.session.query(
            Meeting.language, db.func.count(Meeting.id).label('count')
        ).filter(
            Meeting.user_id == current_user.id,
            Meeting.language.isnot(None),
            Meeting.language != '',
            Meeting.language != 'auto'
        ).group_by(Meeting.language).all()
        stats['languages'] = [{'language': lang, 'count': count} for lang, count in languages]
    except Exception as e:
        logger.error(f"Error getting meeting statistics: {str(e)}")
        stats = {
            'total_meetings': 0,
            'avg_alignment_score': 0,
            'meetings_this_month': 0,
            'languages': []
        }
        flash('Ocorreu um erro ao calcular estatísticas de reuniões.', 'warning')
    return render_template('dashboard.html', meetings=recent_meetings, stats=stats)

def sync_calendar_events_to_meetings(service):
    events = list_upcoming_events(service, max_results=50, include_recent=True)
    event_to_meeting = {}
    for event in events:
        event_id = event.get('id')
        existing = Meeting.query.filter_by(
            user_id=current_user.id,
            google_calendar_event_id=event_id
        ).first()
        if existing:
            event_to_meeting[event_id] = existing.id
            continue
        description = event.get('description', '') or ''
        agenda = ''
        if '--- AGENDA ---' in description:
            agenda = description.split('--- AGENDA ---', 1)[1].strip()
        start_dt = None
        if 'dateTime' in (event.get('start') or {}):
            start_dt = datetime.fromisoformat(
                event['start']['dateTime'].replace('Z', '+00:00')
            )
        created_dt = None
        if event.get('created'):
            created_dt = datetime.fromisoformat(
                event['created'].replace('Z', '+00:00')
            )
        new_meeting = Meeting(
            title=event.get('summary', 'Reunião sem título'),
            agenda=agenda or 'Pauta não definida',
            transcription='',
            meeting_date=start_dt,
            created_at=created_dt,
            user_id=current_user.id,
            google_calendar_event_id=event_id
        )
        db.session.add(new_meeting)
        db.session.commit()
        event_to_meeting[event_id] = new_meeting.id
    return event_to_meeting

@app.route('/meetings')
@login_required
def list_meetings():
    search      = request.args.get('search', '')
    language    = request.args.get('language', '')
    sort_by     = request.args.get('sort_by', 'meeting_date')  # default agora é meeting_date
    sort_order  = request.args.get('sort_order', 'desc')
    show_all    = request.args.get('show_all', 'false').lower() == 'true'
    page        = int(request.args.get('page', 1))

    if not current_user.google_calendar_enabled:
        return render_template('meetings.html',
                               meetings=[],
                               languages=[],
                               current_search=search,
                               current_language=language,
                               current_sort_by=sort_by,
                               current_sort_order=sort_order,
                               show_all=show_all,
                               page=1,
                               total_pages=0)

    # 1) credenciais
    creds   = current_user.get_google_credentials()
    service = build_service(creds)

    # 2) busca TODOS os eventos passados em lotes (paginação da API) e sincroniza
    now_iso    = datetime.utcnow().isoformat() + 'Z'
    all_events = []
    page_token = None
    while True:
        resp = service.events().list(
            calendarId='primary',
            singleEvents=True,
            orderBy='startTime',
            timeMax=now_iso,
            pageToken=page_token,
            maxResults=2500
        ).execute()
        batch = resp.get('items', [])
        all_events.extend(batch)

        for event in batch:
            eid = event.get('id')
            exists = Meeting.query.filter_by(
                user_id=current_user.id,
                google_calendar_event_id=eid
            ).first()
            if not exists:
                desc = event.get('description','') or ''
                if '--- AGENDA ---' in desc:
                    agenda = desc.split('--- AGENDA ---',1)[1].strip()
                else:
                    agenda = 'Pauta não definida'

                sd = event.get('start',{}).get('dateTime')
                dt = datetime.fromisoformat(sd.replace('Z','+00:00')) if sd else None
                cd = event.get('created')
                cdt = datetime.fromisoformat(cd.replace('Z','+00:00')) if cd else None

                m = Meeting(
                    title=event.get('summary','Reunião sem título'),
                    agenda=agenda,
                    transcription='',
                    meeting_date=dt,
                    created_at=cdt,
                    user_id=current_user.id,
                    google_calendar_event_id=eid
                )
                db.session.add(m)
        db.session.commit()

        page_token = resp.get('nextPageToken')
        if not page_token:
            break

    event_ids = [ev['id'] for ev in all_events]

    # 3) Query no banco, só reuniões sincronizadas e já passadas
    if show_all:
        query = Meeting.query.filter_by(user_id=current_user.id)
    else:
        query = Meeting.query.filter(
            Meeting.user_id == current_user.id,
            Meeting.google_calendar_event_id.in_(event_ids)
        )
    query = query.filter(Meeting.meeting_date <= datetime.utcnow())

    # 4) Filtros de pesquisa
    if search:
        query = query.filter(Meeting.title.ilike(f'%{search}%'))
    if language:
        query = query.filter(Meeting.language == language)

    # 5) Ordenação
    col_map = {
        'title':           Meeting.title,
        'alignment_score': Meeting.alignment_score,
        'meeting_date':    Meeting.meeting_date,
        'created_at':      Meeting.created_at
    }
    order_col = col_map.get(sort_by, Meeting.meeting_date)
    if sort_order == 'asc':
        query = query.order_by(order_col.asc())
    else:
        query = query.order_by(order_col.desc())

    # 6) paginação: 10 itens por página
    pagination    = query.paginate(page=page, per_page=10, error_out=False)
    meetings_page = pagination.items
    total_pages   = pagination.pages

    # 7) idiomas existentes para filtro
    langs = (db.session.query(Meeting.language)
             .filter(Meeting.user_id == current_user.id,
                     Meeting.language.isnot(None),
                     Meeting.language != '')
             .distinct().all())
    languages = [l[0] for l in langs if l[0]]

    return render_template(
        'meetings.html',
        meetings=meetings_page,
        languages=languages,
        current_search=search,
        current_language=language,
        current_sort_by=sort_by,
        current_sort_order=sort_order,
        show_all=show_all,
        page=page,
        total_pages=total_pages
    )




@app.route('/new-meeting')
@login_required
def new_meeting():
    """Show form to create a new meeting analysis"""
    return render_template('new_meeting.html')

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    """Process form data and perform meeting analysis"""
    try:
        title = request.form.get('title', 'Reunião sem título')
        agenda = request.form.get('agenda', '')
        transcription = request.form.get('transcription', '')
        meeting_date_str = request.form.get('meeting_date', '')
        
        if not agenda or not transcription:
            flash('Pauta e transcrição são obrigatórios!', 'danger')
            return redirect(url_for('new_meeting'))
        
        # Parse meeting date if provided
        meeting_date = None
        if meeting_date_str:
            try:
                meeting_date = datetime.strptime(meeting_date_str, '%Y-%m-%d')
            except ValueError:
                flash('Formato de data inválido. Use YYYY-MM-DD.', 'warning')
        
        # Log input sizes for debugging
        logger.debug(f"Agenda length: {len(agenda)} characters")
        logger.debug(f"Transcription length: {len(transcription)} characters")
        
        # Analyze the meeting with auto language detection
        results = analyze_meeting(agenda, transcription)
        
        # Create and save the meeting
        lang = results.get('language', '').lower()
        if lang not in ['pt', 'pendente']:
            lang = 'pt'
        meeting = Meeting(
            title=title,
            agenda=agenda,
            transcription=transcription,
            meeting_date=meeting_date,
            user_id=current_user.id,
            language=lang
        )
        
        # Set the results
        meeting.results = results
        
        db.session.add(meeting)
        db.session.commit()
        
        # Also store in session for immediate display
        session['analysis_results'] = results
        
        return redirect(url_for('meeting_detail', meeting_id=meeting.id))
    
    except Exception as e:
        logger.error(f"Error during analysis: {str(e)}")
        flash(f'Ocorreu um erro durante a análise: {str(e)}', 'danger')
        return redirect(url_for('new_meeting'))



def fetch_fireflies_transcript(ff_id):
    """
    Busca o transcript completo da API Fireflies.ai via GraphQL,
    incluindo os campos shorthand_bullet e action_items em summary.
    """
    body = {
        "operationName": "GetTranscript",
        "query": (
            "query GetTranscript($id:String!){"
            "  transcript(id:$id){"
            "    id title date transcript_url audio_url video_url meeting_link duration participants "
            "    summary{"
            "      overview "
            "      bullet_gist "
            "      shorthand_bullet "
            "      action_items"
            "    }"
            "    analytics{"
            "      sentiments{positive_pct neutral_pct negative_pct}"
            "      categories{questions date_times tasks metrics}"
            "      speakers{name duration word_count}"
            "    }"
            "    sentences{index speaker_name text start_time end_time}"
            "  }"
            "}"
        ),
        "variables": {"id": ff_id}
    }
    headers = {
        "Content-Type": "application/json",
        "x-apollo-operation-name": "GetTranscript",
        "Authorization": f"Bearer {os.environ['FIREFLIES_API_TOKEN']}"
    }
    resp = requests.post("https://api.fireflies.ai/graphql", json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()

def fetch_fireflies_id_by_title(title, limit=50):
    """
    Busca na API Fireflies.ai o ID da transcrição correspondente ao título dado.
    """
    body = {
        "operationName": "ListTranscripts",
        "query": (
            "query ListTranscripts($limit:Int,$skip:Int){"
            "  transcripts(limit:$limit,skip:$skip){"
            "    id title date transcript_url audio_url video_url meeting_link duration participants"
            "  }"
            "}"
        ),
        "variables": {"limit": limit, "skip": 0}
    }
    headers = {
        "Content-Type": "application/json",
        "x-apollo-operation-name": "ListTranscripts",
        "Authorization": f"Bearer {os.environ['FIREFLIES_API_TOKEN']}"
    }
    resp = requests.post("https://api.fireflies.ai/graphql", json=body, headers=headers)
    resp.raise_for_status()
    transcripts = resp.json().get("data", {}).get("transcripts", [])
    for tx in transcripts:
        if tx.get("title") == title:
            return tx["id"]
    return None

@app.route('/meetings/<int:meeting_id>')
@login_required
def meeting_detail(meeting_id):
    """
    Mostra o detalhe de uma reunião, incluindo transcript do Fireflies
    (busca primeiro pelo título + data/hora), análise automática,
    notas (shorthand_bullet) e ações sugeridas (action_items) agrupadas por participante.
    """
    meeting = Meeting.query.get_or_404(meeting_id)

    if meeting.user_id != current_user.id:
        flash('Você não tem permissão para acessar esta reunião!', 'danger')
        return redirect(url_for('dashboard'))

    transcript_json   = {"data": {"transcript": {"summary": {}}}}
    ff_summary        = None
    ff_bullets        = None
    fireflies_notes   = []
    fireflies_actions = {}

    # 1) buscar ID + JSON do Fireflies se reunião já ocorreu
    if meeting.meeting_date and meeting.meeting_date <= datetime.utcnow():
        md = meeting.meeting_date
        if md.tzinfo is None:
            md = md.replace(tzinfo=pytz.utc)
        ts_ms = int(md.timestamp() * 1000)

        list_query = """
        query ListTranscripts {
          transcripts { id title date }
        }
        """
        resp = requests.post(
            "https://api.fireflies.ai/graphql",
            json={"operationName": "ListTranscripts", "query": list_query, "variables": {}},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ['FIREFLIES_API_TOKEN']}"
            },
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}) or {}
        transcripts = data.get("transcripts") or []

        chosen_id = next((
            t["id"] for t in transcripts
            if t.get("title","").strip() == meeting.title.strip() and t.get("date") == ts_ms
        ), None)

        if chosen_id:
            meeting.fireflies_transcript_id = chosen_id
            db.session.commit()
            transcript_json = fetch_fireflies_transcript(chosen_id)

    # 2) extrair nó transcript
    tr = transcript_json.get("data", {}).get("transcript", {}) or {}
    tr.setdefault("summary", {})

    # 3) salvar transcription + URLs
    sentences = tr.get("sentences", []) or []
    if sentences:
        meeting.transcription = "\n".join(s.get("text","") for s in sentences)
        meeting.audio_url     = tr.get("audio_url")
        meeting.video_url     = tr.get("video_url")
        db.session.commit()
    elif tr.get("transcript_url"):
        try:
            r2 = requests.get(tr["transcript_url"], timeout=10)
            r2.raise_for_status()
            txt = r2.text.strip()
            if txt:
                meeting.transcription = txt
                db.session.commit()
        except Exception:
            pass

    # 4) gerar análise automática, se necessário
    if meeting.transcription and not meeting.results_json:
        try:
            results = analyze_meeting(meeting.agenda, meeting.transcription)
            meeting.results  = results
            meeting.language = results.get("language", meeting.language)
            db.session.commit()
        except Exception:
            flash('Não foi possível gerar análise automática.', 'warning')

    # 5) extrair overview, bullets e shorthand_bullet (notas)
    summary         = tr.get("summary") or {}
    ff_summary      = summary.get("overview")
    ff_bullets      = summary.get("bullet_gist")
    raw_notes       = summary.get("shorthand_bullet") or ""
    fireflies_notes = [line for line in raw_notes.split("\n") if line.strip()]

    # 6) extrair action_items (string multilinha) e agrupar por participante
    raw = summary.get("action_items") or ""
    current_speaker = None

    for line in raw.splitlines():
        text = line.strip().lstrip("•").strip()
        if not text:
            continue

        # se for nova indicação de speaker (linha em **Nome**)
        if text.startswith("**") and text.endswith("**"):
            current_speaker = text.strip("*")
            continue

        # se for ação e já temos um speaker válido, adiciona à lista
        if current_speaker:
            fireflies_actions.setdefault(current_speaker, []).append(text)

    # 7) renderizar template
    return render_template(
        'results.html',
        results=meeting.results,
        meeting=meeting,
        audio_url=meeting.audio_url,
        video_url=meeting.video_url,
        sentences=(meeting.transcription or "").split("\n"),
        transcript_json=transcript_json,
        fireflies_overview=ff_summary,
        fireflies_bullets=ff_bullets,
        fireflies_notes=fireflies_notes,
        fireflies_actions=fireflies_actions
    )



@app.route('/meetings/<int:meeting_id>/delete', methods=['POST'])
@login_required
def delete_meeting(meeting_id):
    """Delete a meeting"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    # Ensure the meeting belongs to the current user
    if meeting.user_id != current_user.id:
        flash('Você não tem permissão para excluir esta reunião!', 'danger')
        return redirect(url_for('dashboard'))
    
    db.session.delete(meeting)
    db.session.commit()
    
    flash('Reunião excluída com sucesso!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/guest-analyze', methods=['POST'])
def guest_analyze():
    """Process form data for guest users (no login required)"""
    try:
        agenda = request.form.get('agenda', '')
        transcription = request.form.get('transcription', '')
        
        if not agenda or not transcription:
            flash('Pauta e transcrição são obrigatórios!', 'danger')
            return redirect(url_for('index'))
        
        # Log input sizes for debugging
        logger.debug(f"Agenda length: {len(agenda)} characters")
        logger.debug(f"Transcription length: {len(transcription)} characters")
        
        # Analyze the meeting
        results = analyze_meeting(agenda, transcription)
        
        # Store results and original text in session for display
        session['analysis_results'] = results
        session['agenda_text'] = agenda
        session['transcription_text'] = transcription
        
        return redirect(url_for('results'))
    
    except Exception as e:
        logger.error(f"Error during analysis: {str(e)}")
        flash(f'Ocorreu um erro durante a análise: {str(e)}', 'danger')
        return redirect(url_for('index'))

@app.route('/results')
def results():
    """Display analysis results for guest users"""
    if 'analysis_results' not in session:
        flash('Nenhum resultado de análise encontrado. Por favor, envie uma reunião primeiro.', 'warning')
        return redirect(url_for('index'))
    
    results = session['analysis_results']
    return render_template('results.html', results=results, meeting=None)

@app.route('/new-analysis')
def new_analysis():
    """Clear session and start a new analysis"""
    # Limpar todos os dados da análise da sessão
    session_keys = ['analysis_results', 'agenda_text', 'transcription_text']
    for key in session_keys:
        if key in session:
            session.pop(key)
    
    if current_user.is_authenticated:
        return redirect(url_for('new_meeting'))
    else:
        return redirect(url_for('index'))

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {str(e)}")
    
    # Tentar reconectar ao banco de dados em caso de erro de conexão
    try:
        if "OperationalError" in str(e) or "SSL connection" in str(e) or "PendingRollbackError" in str(e):
            logger.warning("Tentando reconectar ao banco de dados...")
            # Tentar fazer rollback para limpar quaisquer transações pendentes
            try:
                db.session.rollback()
                logger.info("Rollback executado com sucesso")
            except Exception as rollback_err:
                logger.error(f"Erro durante rollback: {str(rollback_err)}")
            
            # Tentar remover a sessão atual
            try:
                db.session.remove()
                logger.info("Sessão removida com sucesso")
            except Exception as remove_err:
                logger.error(f"Erro ao remover sessão: {str(remove_err)}")
    except Exception as recovery_err:
        logger.error(f"Erro durante tentativa de recuperação: {str(recovery_err)}")
    
    return render_template('500.html'), 500

# Rotas para a demonstração ao vivo do Web Summit
@app.route('/live-demo')
def live_demo():
    """Página de demonstração ao vivo para o Web Summit"""
    return render_template('live_demo.html', web_summit_agenda=WEB_SUMMIT_AGENDA)

@app.route('/get_web_summit_agenda')
def get_web_summit_agenda():
    """Retorna a pauta padrão do Web Summit"""
    return jsonify({'agenda': WEB_SUMMIT_AGENDA})

@app.route('/get_cake_recipe_agenda')
def get_cake_recipe_agenda():
    """Retorna a pauta alternativa de receita de bolo"""
    return jsonify({'agenda': CAKE_RECIPE_AGENDA})
    
def get_fallback_transcription(demo_type):
    """Retorna uma transcrição simulada para caso a transcrição real falhe"""
    if demo_type == 'standard':
        # Simula uma transcrição que aborda 70% da pauta do Web Summit
        return """Olá a todos, bem-vindos à nossa apresentação no Web Summit.

Somos a equipe da InovAI.lab e hoje vamos falar sobre nosso produto revolucionário, o Transcritor Inteligente com Validação de Pauta.

Sabiam que, em média, as empresas perdem mais de 50 milhões de reais anualmente em reuniões improdutivas? Nosso produto resolve esse problema crítico.

Vamos mostrar agora como funciona nossa plataforma. A interface é intuitiva, permitindo que qualquer pessoa com acesso faça análises de reuniões rapidamente.

Nossas funcionalidades principais incluem a análise automática de alinhamento com a pauta, identificação de tópicos não abordados, e sugestões de próximos passos.

Em testes com nossos primeiros clientes, conseguimos aumentar a eficiência de reuniões em 40% e reduzir o tempo médio gasto em 25%.

Um grande diferencial do nosso produto é o processamento multilíngue automático, que permite análises em português, inglês, espanhol e mais de 10 outros idiomas.

Sobre nossa integração com outras ferramentas, estamos trabalhando com APIs do Microsoft Teams, Zoom e Google Meet.

Temos casos de sucesso em empresas como XYZ Corporation e ABC Enterprises, que viram melhorias significativas na produtividade de reuniões.

Nosso modelo de negócio é baseado em assinaturas mensais e anuais, com planos que variam de acordo com o número de usuários e volume de reuniões.

Temos crescido cerca de 20% ao mês em usuários ativos, e nosso CAC está em aproximadamente 1/3 do LTV.

Para os próximos passos, estamos desenvolvendo recursos de IA generativa para sugerir ações específicas com base no histórico de reuniões.

Isso conclui nossa apresentação principal. Estamos abertos para perguntas e podemos dar mais detalhes sobre como implementar nossa solução na sua organização.

Obrigado pela atenção!"""
    else:
        # Simula uma transcrição que aborda apenas parte da receita de bolo
        return """Bem-vindos ao nosso workshop de confeitaria! Hoje vamos preparar um delicioso bolo de chocolate.

Esta receita vem da minha avó, que era confeiteira profissional nos anos 60. É especial porque combina técnicas tradicionais com um toque moderno.

Vamos começar com os ingredientes. Precisamos de 2 xícaras de farinha de trigo, 1 xícara de açúcar e 1/2 xícara de manteiga.

Infelizmente esqueci de mencionar o leite e o fermento que são essenciais para a receita. Alguém tem alguma dúvida sobre os ingredientes até aqui?

O segredo para um bolo fofo é não bater demais a massa depois de adicionar a farinha. Isso evita o desenvolvimento excessivo do glúten.

O forno deve estar pré-aquecido a 180°C. Um truque é colocar uma assadeira com água no fundo do forno para criar um ambiente úmido.

A propósito, esse bolo combina perfeitamente com um café coado ou até mesmo um chá earl grey.

É ideal para aniversários e também para o lanche da tarde no dia a dia.

Agora, vamos falar sobre a conservação: ele pode ser guardado em temperatura ambiente por até 3 dias, ou na geladeira por uma semana.

Acho que já excedi meu tempo! Alguém tem alguma pergunta sobre o preparo do bolo?"""

@app.route('/process_demo_recording', methods=['POST'])
def process_demo_recording():
    """Processa gravação de áudio da demonstração ao vivo"""
    try:
        # Obtém parâmetros do formulário
        demo_type = request.form.get('demo_type', 'standard')
        duration_seconds = int(request.form.get('duration_seconds', 60))
        custom_agenda = request.form.get('custom_agenda')
        
        # Escolhe a pauta correta com base no tipo de demonstração
        if custom_agenda:
            # Usa a pauta customizada se fornecida
            agenda = custom_agenda
            title = "Demonstração Personalizada"
        elif demo_type == 'standard':
            agenda = WEB_SUMMIT_AGENDA
            title = "Demonstração Web Summit"
        else:
            agenda = CAKE_RECIPE_AGENDA
            title = "Demonstração Receita de Bolo"
        
        # Verificar se existe arquivo de áudio no request
        audio_file = request.files.get('audio_file')
        
        # Logs detalhados para depuração
        if audio_file:
            logger.debug(f"Arquivo de áudio recebido: {audio_file}, nome: {audio_file.filename}, tipo: {audio_file.content_type if hasattr(audio_file, 'content_type') else 'N/A'}")
            try:
                tamanho = audio_file.tell() if hasattr(audio_file, 'tell') else "N/A"
                logger.debug(f"Tamanho do arquivo de áudio: {tamanho}")
            except:
                logger.debug("Não foi possível determinar o tamanho do arquivo de áudio")
        else:
            logger.debug("Nenhum arquivo de áudio encontrado na solicitação")
        
        # Lista completa dos campos no formulário para depuração
        logger.debug(f"Campos do formulário: {list(request.form.keys())}")
        logger.debug(f"Campos de arquivo: {list(request.files.keys())}")
        
        # Variável para rastrear se estamos usando transcrição real ou simulada
        using_fallback = False
        
        # Se tiver arquivo de áudio, verifica tamanho para decidir se faz transcrição real
        if audio_file and audio_file.filename:
            try:
                # Restaurar o ponteiro do arquivo para o início e verificar seu tamanho
                if hasattr(audio_file, 'seek'):
                    audio_file.seek(0)
                
                # Ler os dados para verificar o tamanho sem gastar o stream
                audio_data = audio_file.read()
                file_size_mb = len(audio_data) / (1024 * 1024)
                logger.debug(f"Tamanho do arquivo de áudio: {file_size_mb:.2f} MB")
                
                # Para ambiente Replit, definir um limite bem conservador
                # para evitar timeouts (que são muito frequentes com a API da OpenAI)
                if file_size_mb > 3.0:
                    logger.warning(f"Arquivo de áudio muito grande ({file_size_mb:.2f} MB), usando transcrição simulada")
                    transcription = get_fallback_transcription(demo_type)
                    flash("O arquivo de áudio é muito grande para processamento. Em um ambiente de produção completo, "
                          "seria possível fazer a transcrição, mas para esta demonstração estamos usando uma transcrição simulada.", 
                          "warning")
                    using_fallback = True
                else:
                    # Apenas para arquivos pequenos, tentamos transcrição real
                    logger.debug("Iniciando transcrição do áudio (arquivo pequeno)")
                    
                    # Recriar o arquivo in-memory para a API
                    from io import BytesIO
                    audio_file_for_api = BytesIO(audio_data)
                    audio_file_for_api.name = "audio.webm"  # Nome necessário para o OpenAI identificar o formato
                    
                    try:
                        # Tenta a transcrição com timeout rigoroso
                        transcription = transcribe_audio(audio_file_for_api, max_file_size_mb=3)
                        
                        if not transcription or len(transcription.strip()) < 10:
                            logger.warning("Transcrição retornou vazia ou muito curta, usando fallback")
                            transcription = get_fallback_transcription(demo_type)
                            using_fallback = True
                        else:
                            logger.debug(f"Transcrição real realizada com sucesso: {transcription[:100]}...")
                    except Exception as e:
                        logger.error(f"Erro na transcrição do áudio: {str(e)}")
                        transcription = get_fallback_transcription(demo_type)
                        using_fallback = True
                        flash(f"Erro ao transcrever o áudio: {str(e)}. Usando transcrição simulada.", "warning")
            except Exception as e:
                logger.error(f"Erro ao processar arquivo de áudio: {str(e)}")
                transcription = get_fallback_transcription(demo_type)
                using_fallback = True
                flash(f"Erro ao processar o arquivo de áudio: {str(e)}. Usando transcrição simulada.", "warning")
        else:
            # Se não tiver áudio, usa a transcrição simulada
            logger.debug("Nenhum arquivo de áudio válido recebido, usando transcrição simulada")
            transcription = get_fallback_transcription(demo_type)
            using_fallback = True
            
        # Adicionar aviso se estiver usando transcrição simulada
        if using_fallback:
            logger.warning("Atenção: Usando transcrição SIMULADA para demonstração")
            flash("Atenção: A demonstração está usando uma transcrição simulada devido a limitações técnicas. Em um ambiente de produção, a transcrição seria feita com seu áudio real.", "warning")
            
        # Adicionar mensagem de depuração para mostrar o que será analisado
        logger.debug(f"Analisando transcrição com {len(transcription)} caracteres")
        
        try:
            # Analisar a reunião com limite de tamanho para evitar timeout
            logger.debug("Chamando analyze_meeting com limite de tamanho")
            results = analyze_meeting(agenda, transcription, max_transcription_length=6000)
            
            # Log para indicar se a transcrição foi truncada
            if results.get('truncated_transcription'):
                logger.warning(f"A transcrição foi truncada de {results.get('original_length')} para {results.get('analysis_length')} caracteres")
                flash("Nota: A transcrição era muito longa e foi truncada para análise. Algumas partes da reunião podem não ter sido analisadas completamente.", "warning")
                
        except Exception as e:
            logger.error(f"Erro na análise da reunião: {str(e)}")
            # Criar um resultado básico em caso de erro para que a página não quebre
            results = {
                "agenda_items": [{"item": item.strip(), "addressed": False, "context": "Não foi possível analisar devido a um erro"} 
                               for item in agenda.split('\n') if item.strip()],
                "unaddressed_items": [],
                "additional_topics": [],
                "meeting_summary": "Ocorreu um erro ao analisar a reunião. O texto pode ser muito longo ou complexo.",
                "alignment_score": 0,
                "insights": ["Ocorreu um erro na análise. Por favor, tente novamente com uma transcrição menor."],
                "next_steps": ["Tente novamente com uma gravação mais curta"],
                "action_items": [],
                "directions": [],
                "language": "pt",
                "error": str(e)
            }
            flash(f"Ocorreu um erro ao analisar a reunião: {str(e)}. Usando resultados básicos.", "danger")
        
        # Calcular estatísticas para exibição
        agenda_items = results.get('agenda_items', [])
        total_items = len(agenda_items)
        addressed_items = sum(1 for item in agenda_items if item.get('addressed', False))
        
        # Calcular custo estimado baseado no tempo de duração
        participants = 3  # Número simulado de participantes para demo
        hours = duration_seconds / 3600  # Converter segundos para horas
        estimated_cost = hours * HOURLY_COST * participants
        
        # Armazenar na sessão para acesso posterior
        session['last_demo_results'] = results
        session['last_demo_agenda'] = agenda
        session['last_demo_transcription'] = transcription
        
        # Configurar os dados para resposta
        meeting_id = None
        alignment_score = results.get('alignment_score', 0)
        
        if current_user.is_authenticated:
            # Criar registro de reunião para a demonstração
            lang = results.get('language', '').lower()
            if lang not in ['pt', 'pendente']:
                lang = 'pt'
            meeting = Meeting(
                title=title,
                agenda=agenda,
                transcription=transcription,
                user_id=current_user.id,
                meeting_date=datetime.utcnow(),
                created_at=datetime.utcnow(),
                alignment_score=alignment_score,
                language=lang
            )
            # Armazenar resultados
            meeting.results = results
            # Salvar a reunião no banco de dados
            db.session.add(meeting)
            db.session.commit()
            meeting_id = meeting.id
        
        # Retornar resultados para a interface
        return jsonify({
            'meeting_id': meeting_id,
            'alignment_score': alignment_score,
            'addressed_items': addressed_items,
            'total_items': total_items,
            'estimated_cost': estimated_cost,
            'duration_seconds': duration_seconds
        })
        
    except Exception as e:
        logger.error(f"Erro no processamento da demonstração: {str(e)}")
        return jsonify({
            'error': 'Ocorreu um erro ao processar a gravação',
            'details': str(e)
        }), 500

@app.route('/demo-results')
def demo_results():
    """Página de resultados para a última demonstração (para usuários não logados)"""
    if 'last_demo_results' not in session:
        flash('Nenhum resultado de demonstração encontrado. Por favor, realize uma demonstração primeiro.', 'warning')
        return redirect(url_for('live_demo'))
    
    results = session['last_demo_results']
    agenda = session.get('last_demo_agenda', '')
    transcription = session.get('last_demo_transcription', '')
    
    # Determinar o título da demonstração com base no conteúdo da agenda
    if "Web Summit" in agenda:
        demo_title = "Demonstração Web Summit"
    elif "Receita de Bolo" in agenda:
        demo_title = "Demonstração Receita de Bolo"
    else:
        demo_title = "Demonstração Personalizada"
        
    # Criar um objeto similar a uma instância de Meeting para usar o mesmo template
    demo_meeting = {
        'id': None,
        'title': demo_title,
        'agenda': agenda,
        'transcription': transcription,
        'meeting_date': datetime.utcnow(),
        'created_at': datetime.utcnow(),
        'language': results.get('language', 'pt'),
        'alignment_score': results.get('alignment_score', 0),
        'is_demo': True  # Marcador para indicar que é uma demonstração (não salva)
    }
    
    return render_template('results.html', results=results, meeting=demo_meeting)


#############################################################
# Google Calendar Integration
#############################################################
from google_calendar import (
    get_authorization_url, get_credentials_from_code,
    build_service, list_upcoming_events, create_meeting_event,
    get_redirect_uri
)

@app.route('/settings')
@login_required
def settings():
    """Página de configurações da conta do usuário"""
    return render_template('settings.html')

@app.route('/settings/google_calendar_connect')
@login_required
def settings_google_calendar_connect():
    """Iniciar processo de autorização do Google Calendar"""
    try:
        # Check for required environment variables
        if not os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or not os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"):
            flash("É necessário configurar as credenciais do Google OAuth. Contate o administrador do sistema.", "danger")
            return redirect(url_for('settings'))
            
        # Generate authorization URL
        authorization_url, state = get_authorization_url()
        
        # Store state in session for later validation
        session['oauth_state'] = state
        
        # Redirect user to Google's OAuth page
        return redirect(authorization_url)
        
    except Exception as e:
        logger.error(f"Error connecting to Google Calendar: {str(e)}")
        flash(f"Erro ao conectar ao Google Calendar: {str(e)}", "danger")
        return redirect(url_for('settings'))

@app.route('/settings/google_callback')
@login_required
def settings_google_callback():
    """Callback para autorização do Google OAuth"""
    try:
        # Get authorization code from request
        code = request.args.get('code')
        if not code:
            logger.error("Authorization code was missing in the request")
            flash("Autorização cancelada ou inválida.", "warning")
            return redirect(url_for('settings'))
            
        # Log para depuração
        logger.debug(f"Recebido código de autorização do Google")
        logger.debug(f"Ambiente: {'Produção' if os.environ.get('REPLIT_DEPLOYMENT_ID') else 'Desenvolvimento'}")
        logger.debug(f"URI de redirecionamento configurado: {os.environ.get('REDIRECT_URI', get_redirect_uri())}")
        
        try:
            # Exchange code for tokens
            credentials = get_credentials_from_code(code)
            
            # Save credentials to user model
            current_user.set_google_credentials(credentials)
            db.session.commit()
            
            flash("Google Calendar conectado com sucesso!", "success")
            return redirect(url_for('settings'))
            
        except Exception as token_error:
            logger.error(f"Error exchanging code for token: {str(token_error)}")
            logger.error(f"Detalhes técnicos: {type(token_error).__name__}")
            
            # Fornecer uma mensagem mais específica com base no tipo de erro
            if "invalid_grant" in str(token_error).lower():
                flash("Erro de autenticação: o código de autorização expirou ou já foi utilizado. Por favor, tente novamente.", "danger")
            elif "redirect_uri_mismatch" in str(token_error).lower():
                flash("Erro de configuração: a URL de redirecionamento não corresponde à configurada no Google Cloud Console.", "danger")
            else:
                flash(f"Erro ao processar autorização: {str(token_error)}", "danger")
                
            return redirect(url_for('settings'))
            
    except Exception as e:
        logger.error(f"Erro geral no callback do Google OAuth: {str(e)}")
        logger.error(f"Detalhes técnicos: {type(e).__name__}")
        flash(f"Erro ao processar autorização: {str(e)}", "danger")
        return redirect(url_for('settings'))

@app.route('/settings/google_calendar_disconnect')
@login_required
def settings_google_calendar_disconnect():
    """Remover integração com o Google Calendar"""
    try:
        # Clear Google credentials
        current_user.set_google_credentials(None)
        db.session.commit()
        
        flash("Google Calendar desconectado.", "success")
        return redirect(url_for('settings'))
        
    except Exception as e:
        logger.error(f"Error disconnecting Google Calendar: {str(e)}")
        flash(f"Erro ao desconectar: {str(e)}", "danger")
        return redirect(url_for('settings'))





@app.route('/calendar')
@login_required
def view_calendar():
    """Ver eventos do Google Calendar"""
    if not current_user.google_calendar_enabled:
        flash("Você precisa conectar sua conta do Google Calendar primeiro.", "warning")
        return redirect(url_for('settings'))
        
    try:
        # Get user's Google credentials
        credentials = current_user.get_google_credentials()
        service = build_service(credentials)

        # Sincroniza eventos do calendário com reuniões no banco
        sync_calendar_events_to_meetings(service)

        # Buscar eventos do Google Calendar (futuros e passados)
        events = list_upcoming_events(service, max_results=50, include_recent=True)

        # Próxima reunião: evento futuro mais próximo
        from datetime import timezone
        now_dt = datetime.now(timezone.utc)
        next_meeting = None
        future_events = [ev for ev in events if 'dateTime' in (ev.get('start') or {}) and datetime.fromisoformat(ev['start']['dateTime'].replace('Z', '+00:00')) > now_dt]
        if future_events:
            next_meeting = sorted(future_events, key=lambda ev: ev['start']['dateTime'])[0]

        # Reuniões recentes: últimos 10 eventos passados
        recent_meetings = [ev for ev in events if 'dateTime' in (ev.get('start') or {}) and datetime.fromisoformat(ev['start']['dateTime'].replace('Z', '+00:00')) <= now_dt]
        recent_meetings = sorted(recent_meetings, key=lambda ev: ev['start']['dateTime'], reverse=True)[:10]

        # Obter data e hora atual no fuso horário de São Paulo para comparação no template
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        now = datetime.now(sao_paulo_tz).isoformat()

        # Verificar quais eventos já foram analisados
        analyzed_events = {}
        from sqlalchemy import and_, or_, not_
        analyzed_meetings = Meeting.query.filter(
            Meeting.user_id == current_user.id,
            Meeting.results_json != None
        ).all()
        for meeting in analyzed_meetings:
            analyzed_events[meeting.title] = meeting.id

        return render_template('calendar.html',
            events=events,
            current_time=now,
            analyzed_events=analyzed_events,
            next_meeting=next_meeting,
            recent_meetings=recent_meetings
        )
    except Exception as e:
        logger.error(f"Error fetching calendar events: {str(e)}")
        flash(f"Erro ao buscar eventos do calendário: {str(e)}", "danger")
        return redirect(url_for('settings'))

@app.route('/calendar/event/<event_id>')
@login_required
def event_details(event_id):
    """Ver detalhes de um evento do Google Calendar"""
    if not current_user.google_calendar_enabled:
        flash("Você precisa conectar sua conta do Google Calendar primeiro.", "warning")
        return redirect(url_for('settings'))
        
    try:
        # Get user's Google credentials
        credentials = current_user.get_google_credentials()
        
        # Build Google Calendar service
        service = build_service(credentials)
        
        # Get event details with the correct timezone
        event = service.events().get(calendarId='primary', eventId=event_id, timeZone='America/Sao_Paulo').execute()
        
        # Determine if event already happened (can be analyzed) usando fuso horário local
        import pytz
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        now = datetime.now(sao_paulo_tz).isoformat()
        event_end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date'))
        can_analyze = event_end < now if event_end else False
        
        return render_template('event_details.html', event=event, can_analyze=can_analyze)
        
    except Exception as e:
        logger.error(f"Error fetching event details: {str(e)}")
        flash(f"Erro ao buscar detalhes do evento: {str(e)}", "danger")
        return redirect(url_for('view_calendar'))
    

@app.route('/create_event', methods=['POST'])
@login_required
def create_event():
    """Criar novo evento no Google Calendar (apenas via modal)"""
    if not current_user.google_calendar_enabled:
        flash("Você precisa conectar sua conta do Google Calendar primeiro.", "warning")
        return redirect(url_for('view_calendar'))

    try:
        # 1) Dados do formulário
        title            = request.form.get('title')
        description      = request.form.get('description', '')
        start_date       = request.form.get('start_date', '').strip()
        start_time       = request.form.get('start_time', '').strip()
        end_time         = request.form.get('end_time', '').strip()
        all_day          = request.form.get('all_day') is not None

        # 2) Validações
        if not start_date:
            raise ValueError("Data é obrigatória")
        if all_day:
            start_time, end_time = "00:00", "23:59"
        elif not start_time or not end_time:
            raise ValueError("Hora de início e hora de término são obrigatórias")

        # 3) Conversão para datetime
        start_datetime = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
        end_datetime   = datetime.strptime(f"{start_date} {end_time}",   "%Y-%m-%d %H:%M")

        # 4) Participantes
        attendees_raw = request.form.get('attendees', '')
        attendees = []
        if attendees_raw:
            import re
            attendees = [
                e.strip() for e in re.split(r'[,;\s]+', attendees_raw)
                if '@' in e
            ]
        if "hub@inovailab.com" not in attendees:
            attendees.append("hub@inovailab.com")

        # 5) Descrição e pauta
        agenda = request.form.get('agenda', '')
        full_description = (
            f"{description}\n\n--- PAUTA ---\n{agenda}"
            if agenda else description
        )

        # 6) Recorrência
        recurrence_type        = request.form.get('recurrence_type', 'none')
        recurrence_count_value = request.form.get('recurrence_count', '730')
        recurrence_count = (
            'unlimited'
            if recurrence_count_value == 'unlimited'
            else int(recurrence_count_value)
        )

        recurrence_data = None
        if recurrence_type != 'none':
            # mapeia dia da semana do start_datetime
            weekday_map   = ['MO','TU','WE','TH','FR','SA','SU']
            dia_da_semana = weekday_map[start_datetime.weekday()]

            if recurrence_type == 'daily':
                weekdays = ['MO','TU','WE','TH','FR']
            elif recurrence_type == 'weekly':
                weekdays = [dia_da_semana]
            else:
                weekdays = []

            recurrence_data = {
                'type':     recurrence_type,  # 'daily' ou 'weekly'
                'interval': 1,
                'count':    recurrence_count,
                'weekdays': weekdays
            }

        # 7) Chama o utilitário de criação
        credentials = current_user.get_google_credentials()
        from calendar_utils import build_calendar_service, create_calendar_event
        service = build_calendar_service(credentials)
        event = create_calendar_event(
            service=service,
            title=title,
            description=full_description,
            start_time=start_datetime,
            end_time=end_datetime,
            attendees=attendees,
            recurrence=recurrence_data
        )

        # 8) Feedback
        if recurrence_type == 'none':
            flash('Reunião agendada com sucesso!', 'success')
        else:
            if recurrence_count == 'unlimited':
                msg = 'Reunião recorrente criada com sucesso – repetição infinita.'
            else:
                msg = f'Reunião recorrente criada com sucesso – {recurrence_count} ocorrências.'
            flash(msg, 'success')

        return redirect(url_for('view_calendar'))

    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        flash(f"Erro ao criar evento: {e}", 'danger')
        return redirect(url_for('view_calendar'))
    
@app.route('/generate_agenda', methods=['GET', 'POST'])
@login_required
def generate_agenda():
    """Gerar pauta de reunião com inteligência artificial"""
    if not current_user.google_calendar_enabled:
        flash('Você precisa conectar sua conta do Google Calendar primeiro.', 'warning')
        return redirect(url_for('settings'))
    
    if request.method == 'POST':
        topic = request.form.get('topic', '')
        description = request.form.get('description', '')
        language = request.form.get('language', 'pt')
        
        if not topic or not description:
            flash('Por favor, informe o tópico e a descrição da reunião.', 'warning')
            return redirect(url_for('generate_agenda'))
        
        try:
            # Gerar pauta usando a OpenAI
            generated_data = generate_meeting_agenda(topic, description, language)
            
            # Salvar na sessão para exibição na próxima página
            session['generated_title'] = generated_data.get('title', topic)
            session['generated_agenda'] = generated_data.get('agenda', '')
            
            # Redirecionar para a página de edição da pauta
            return redirect(url_for('edit_agenda'))
        
        except Exception as e:
            logger.error(f"Erro ao gerar pauta: {str(e)}")
            flash(f'Erro ao gerar pauta: {str(e)}', 'danger')
            return redirect(url_for('generate_agenda'))
    
    return render_template('generate_agenda.html')

@app.route('/edit_agenda', methods=['GET', 'POST'])
@login_required
def edit_agenda():
    """Editar a pauta gerada e criar evento no Google Calendar"""
    if not current_user.google_calendar_enabled:
        flash('Você precisa conectar sua conta do Google Calendar primeiro.', 'warning')
        return redirect(url_for('settings'))
    
    # Verificar se existem dados de pauta na sessão
    if 'generated_title' not in session or 'generated_agenda' not in session:
        flash('Nenhuma pauta gerada. Por favor, gere uma pauta primeiro.', 'warning')
        return redirect(url_for('generate_agenda'))
    
    if request.method == 'POST':
        title = request.form.get('title', '')
        agenda = request.form.get('agenda', '')
        description = request.form.get('description', '')
        start_date = request.form.get('start_date', '')
        start_time = request.form.get('start_time', '')
        end_date = request.form.get('end_date', '')
        end_time = request.form.get('end_time', '')
        attendees = request.form.get('attendees', '')
        
        if not title or not agenda or not start_date or not start_time or not end_date or not end_time:
            flash('Todos os campos marcados com * são obrigatórios.', 'warning')
            return render_template('edit_agenda.html', 
                                   title=title or session['generated_title'],
                                   agenda=agenda or session['generated_agenda'],
                                   description=description)
        
        try:
            # Converter data e hora para datetime
            start_datetime = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
            end_datetime = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
            
            # Verificar se a data de término é posterior à data de início
            if end_datetime <= start_datetime:
                flash('A hora de término deve ser posterior à hora de início.', 'warning')
                return render_template('edit_agenda.html', 
                                       title=title,
                                       agenda=agenda,
                                       description=description)
            
            # Processar lista de participantes
            attendees_list = []
            if attendees:
                attendees_list = [email.strip() for email in attendees.split(',') if email.strip()]
            
            # Garantir que hub@inovailab.com sempre seja chamado apenas uma vez
            if "hub@inovailab.com" not in attendees_list:
                attendees_list.append("hub@inovailab.com")
            
            # Obter as credenciais do Google
            credentials_data = current_user.get_google_credentials()
            service = build_service(credentials_data)
            
            # Preparar a descrição completa do evento com a pauta
            full_description = f"{description}\n\n--- AGENDA ---\n{agenda}"
            
            # Criar evento no Google Calendar
            event = create_meeting_event(
                service=service,
                title=title,
                description=full_description,
                start_time=start_datetime,
                end_time=end_datetime,
                attendees=attendees_list
            )
            
            # Limpar dados da sessão
            session.pop('generated_title', None)
            session.pop('generated_agenda', None)
            
            flash('Evento criado com sucesso no Google Calendar!', 'success')
            return redirect(url_for('event_details', event_id=event['id']))
            
        except Exception as e:
            logger.error(f"Erro ao criar evento: {str(e)}")
            flash(f'Erro ao criar evento: {str(e)}', 'danger')
            return render_template('edit_agenda.html', 
                                   title=title,
                                   agenda=agenda,
                                   description=description)
    
    # Obter a data atual formatada para o template com o fuso horário de São Paulo
    import pytz
    sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
    today_date = datetime.now(sao_paulo_tz).strftime("%Y-%m-%d")
    
    # Exibir formulário com dados gerados
    return render_template('edit_agenda.html',
                           title=session['generated_title'],
                           agenda=session['generated_agenda'],
                           description="",
                           today_date=today_date)


@app.route('/calendar/event/<event_id>/analyze')
@login_required
def analyze_calendar_event(event_id):
    """Analisar uma reunião do Google Calendar"""
    if not current_user.google_calendar_enabled:
        flash("Você precisa conectar sua conta do Google Calendar primeiro.", "warning")
        return redirect(url_for('settings'))
        
    try:
        # Get user's Google credentials
        credentials = current_user.get_google_credentials()
        
        # Build Google Calendar service
        service = build_service(credentials)
        
        # Get event details with the correct timezone
        event = service.events().get(calendarId='primary', eventId=event_id, timeZone='America/Sao_Paulo').execute()
        
        # Extract agenda from description
        description = event.get('description', '')
        agenda = ""
        
        if "--- AGENDA ---" in description:
            agenda = description.split("--- AGENDA ---")[1].strip()
        
        # Create a new meeting entry with a placeholder for the transcription
        meeting = Meeting(
            title=event.get('summary', 'Reunião sem título'),
            agenda=agenda if agenda else "Pauta não definida",
            transcription="Para analisar esta reunião, insira a transcrição",
            user_id=current_user.id,
            meeting_date=datetime.fromisoformat(event.get('start', {}).get('dateTime', '').replace('Z', '+00:00')),
            google_calendar_event_id=event_id  # Salvar o ID do evento do Google Calendar
        )
        
        db.session.add(meeting)
        db.session.commit()
        
        # Redirect to the form page for adding transcription
        flash("Por favor, adicione a transcrição da reunião para análise.", "info")
        return redirect(url_for('edit_calendar_analysis', meeting_id=meeting.id))
        
    except Exception as e:
        logger.error(f"Error analyzing calendar event: {str(e)}")
        flash(f"Erro ao analisar evento: {str(e)}", "danger")
        return redirect(url_for('view_calendar'))
        
@app.route('/meetings/<int:meeting_id>/edit-calendar-analysis')
@login_required
def edit_calendar_analysis(meeting_id):
    """Página para adicionar transcrição de uma reunião do Google Calendar"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    # Ensure the meeting belongs to the current user
    if meeting.user_id != current_user.id:
        flash('Você não tem permissão para acessar esta reunião!', 'danger')
        return redirect(url_for('dashboard'))
    
    # Renderizar o template com o formulário para adicionar a transcrição
    return render_template('analyze_calendar_meeting.html', meeting=meeting)

@app.route('/meetings/<int:meeting_id>/process-calendar-analysis', methods=['POST'])
@login_required
def process_calendar_analysis(meeting_id):
    """Processar a análise de uma reunião do Google Calendar com a transcrição fornecida"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    # Ensure the meeting belongs to the current user
    if meeting.user_id != current_user.id:
        flash('Você não tem permissão para acessar esta reunião!', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        # Obter a transcrição do formulário
        transcription = request.form.get('transcription', '')
        
        if not transcription or transcription == "Para analisar esta reunião, insira a transcrição":
            flash('A transcrição da reunião é obrigatória!', 'warning')
            return redirect(url_for('edit_calendar_analysis', meeting_id=meeting_id))
        
        # Log input sizes for debugging
        logger.debug(f"Agenda length: {len(meeting.agenda)} characters")
        logger.debug(f"Transcription length: {len(transcription)} characters")
        
        # Analisar a reunião com detecção automática de idioma
        results = analyze_meeting(meeting.agenda, transcription)
        
        # Atualizar os dados da reunião
        meeting.transcription = transcription
        meeting.results = results
        lang = results.get('language', '').lower()
        if lang not in ['pt', 'pendente']:
            lang = 'pt'
        meeting.language = lang
        meeting.alignment_score = results.get('alignment_score', 0)
        
        db.session.commit()
        
        # Armazenar os resultados na sessão para exibição imediata
        session['analysis_results'] = results
        
        flash('Análise realizada com sucesso!', 'success')
        return redirect(url_for('meeting_detail', meeting_id=meeting.id))
        
    except Exception as e:
        logger.error(f"Error during calendar meeting analysis: {str(e)}")
        flash(f'Ocorreu um erro durante a análise: {str(e)}', 'danger')
        return redirect(url_for('edit_calendar_analysis', meeting_id=meeting_id))