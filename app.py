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
import requests


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

# Initialize database
db.init_app(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Register template filters
from filters import filters_bp
app.register_blueprint(filters_bp)

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception as e:
        logger.error(f"Error loading user: {str(e)}")
        return None

# Create tables if they don't exist
with app.app_context():
    db.create_all()
    logger.debug("Database tables created or confirmed")

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
        
        flash('Registro realizado com sucesso! Agora você pode fazer login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

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
        
        # Get language distribution
        languages = db.session.query(
            Meeting.language, db.func.count(Meeting.id).label('count')
        ).filter(
            Meeting.user_id == current_user.id
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

@app.route('/meetings')
@login_required
def list_meetings():
    """List all user meetings with filtering and sorting options"""
    # Get filter parameters
    search = request.args.get('search', '')
    language = request.args.get('language', '')
    sort_by = request.args.get('sort_by', 'created_at')
    sort_order = request.args.get('sort_order', 'desc')
    show_all = request.args.get('show_all', 'false').lower() == 'true'
    
    # Sincronizar reuniões do Google Calendar quando conectado
    if current_user.google_calendar_enabled:
        try:
            # Get user's Google credentials
            credentials = current_user.get_google_credentials()
            
            # Build Google Calendar service
            service = build_service(credentials)
            
            # Sincroniza eventos do calendário com reuniões no banco
            sync_calendar_events_to_meetings(service)
        except Exception as e:
            logger.error(f"Error syncing calendar events to meetings: {str(e)}")
            # Continue without syncing if there's an error
    
    # Base query
    query = Meeting.query.filter_by(user_id=current_user.id)
    
    # Filtrar reuniões do Google Calendar conectado atual
    if current_user.google_calendar_enabled and not show_all:
        # Lista para armazenar IDs dos eventos do calendário atual
        current_calendar_event_ids = []
        
        try:
            # Get user's Google credentials
            credentials = current_user.get_google_credentials()
            
            # Build Google Calendar service
            service = build_service(credentials)
            
            # Get events from this calendar (past week and upcoming)
            events = list_upcoming_events(service, max_results=50, include_recent=True)
            
            # Extract event IDs
            for event in events:
                current_calendar_event_ids.append(event['id'])
                
            if current_calendar_event_ids:
                # Exibe apenas reuniões com evento_id nulo (reuniões criadas manualmente) OU
                # reuniões com evento_id no calendário atual
                query = query.filter(
                    db.or_(
                        Meeting.google_calendar_event_id.is_(None),
                        Meeting.google_calendar_event_id.in_(current_calendar_event_ids)
                    )
                )
                
        except Exception as e:
            logger.error(f"Error fetching calendar events for filtering: {str(e)}")
            # Em caso de erro, continua sem filtrar
    
    # Apply search filter (title)
    if search:
        query = query.filter(Meeting.title.ilike(f'%{search}%'))
    
    # Apply language filter
    if language:
        query = query.filter(Meeting.language == language)
    
    # Apply sorting
    if sort_by == 'title':
        order_column = Meeting.title
    elif sort_by == 'alignment_score':
        order_column = Meeting.alignment_score
    elif sort_by == 'meeting_date':
        order_column = Meeting.meeting_date
    else:  # default: created_at
        order_column = Meeting.created_at
    
    if sort_order == 'asc':
        query = query.order_by(order_column.asc())
    else:
        query = query.order_by(order_column.desc())
    
    # Get all meetings
    meetings = query.all()
    
    # Get unique languages for the filter dropdown
    languages = db.session.query(Meeting.language).filter(
        Meeting.user_id == current_user.id,
        Meeting.language != None,
        Meeting.language != ''
    ).distinct().all()
    
    languages = [l[0] for l in languages if l[0]]
    
    return render_template('meetings.html', 
                          meetings=meetings, 
                          languages=languages,
                          current_search=search,
                          current_language=language,
                          current_sort_by=sort_by,
                          current_sort_order=sort_order,
                          show_all=show_all)

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
        meeting = Meeting(
            title=title,
            agenda=agenda,
            transcription=transcription,
            meeting_date=meeting_date,
            user_id=current_user.id,
            language=results.get('language', 'auto')
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
    Busca o transcript completo da API Fireflies.ai via GraphQL.
    Retorna o JSON bruto exatamente como no Postman.
    """
    query = """
    query GetTranscript($id: String!) {
      transcript(id: $id) {
        id
        title
        date
        transcript_url
        audio_url
        video_url
        meeting_link
        duration
        participants
        summary {
          overview
          bullet_gist
        }
        analytics {
          sentiments {
            positive_pct
            neutral_pct
            negative_pct
          }
          categories {
            questions
            date_times
            tasks
            metrics
          }
          speakers {
            name
            duration
            word_count
          }
        }
        sentences {
          index
          speaker_name
          text
          start_time
          end_time
        }
      }
    }
    """

    body = {
        "operationName": "GetTranscript",
        "query": query,
        "variables": {"id": ff_id}
    }
    headers = {
        "Content-Type": "application/json",
        "x-apollo-operation-name": "GetTranscript",
        "Authorization": f"Bearer {os.environ['FIREFLIES_API_TOKEN']}"
    }

    resp = requests.post(
        "https://api.fireflies.ai/graphql",
        json=body,
        headers=headers
    )
    resp.raise_for_status()
    return resp.json()


@app.route('/meetings/<int:meeting_id>')
@login_required
def meeting_detail(meeting_id):
    """Mostra o detalhe de uma reunião, incluindo transcript do Fireflies."""
    meeting = Meeting.query.get_or_404(meeting_id)
    if meeting.user_id != current_user.id:
        flash('Você não tem permissão para acessar esta reunião!', 'danger')
        return redirect(url_for('dashboard'))

    ff_id = meeting.results.get('transcript', {}).get('id')
    if not ff_id:
        flash('Transcript ID não encontrado. Não é possível buscar no Fireflies.', 'warning')
        return render_template(
            'results.html',
            results=meeting.results,
            meeting=meeting,
            audio_url=None,
            video_url=None,
            sentences=[]
        )

    transcript_json = None
    audio_url = None
    video_url = None
    sentences = []

    try:
        transcript_json = fetch_fireflies_transcript(ff_id)
        tr = transcript_json.get("data", {}).get("transcript")
        if tr:
            audio_url = tr.get("audio_url")
            video_url = tr.get("video_url")
            sentences = [s.get("text", "") for s in tr.get("sentences", [])]
        else:
            logger.error(f"Fireflies retornou transcript null: {transcript_json!r}")
            flash(
                f"Transcrição externa retornou vazia. Resposta da API: {transcript_json}",
                "warning"
            )
    except Exception as e:
        logger.exception("Erro ao buscar transcript Fireflies")
        flash(f"Erro ao obter transcrição externa: {e}", "warning")

    return render_template(
        'results.html',
        results=meeting.results,
        meeting=meeting,
        audio_url=audio_url,
        video_url=video_url,
        sentences=sentences,
        transcript_json=transcript_json
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
            meeting = Meeting(
                title=title,
                agenda=agenda,
                transcription=transcription,
                user_id=current_user.id,
                meeting_date=datetime.utcnow(),
                created_at=datetime.utcnow(),
                alignment_score=alignment_score,
                language=results.get('language', 'pt')
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

def sync_calendar_events_to_meetings(service):
    """
    Sincroniza eventos do Google Calendar para a tabela de reuniões
    
    Args:
        service: Serviço do Google Calendar
        
    Returns:
        dict: Mapeamento de IDs de eventos para IDs de reuniões
    """
    # Obter eventos do calendário
    events = list_upcoming_events(service, max_results=50, include_recent=True)
    
    # Mapear IDs de eventos para IDs de reuniões
    event_to_meeting = {}
    
    for event in events:
        event_id = event.get('id')
        
        # Verificar se o evento já está nas reuniões
        existing_meeting = Meeting.query.filter_by(
            user_id=current_user.id,
            google_calendar_event_id=event_id
        ).first()
        
        if existing_meeting:
            # Se já existe, apenas atualize o mapeamento
            event_to_meeting[event_id] = existing_meeting.id
        else:
            # Se não existe, crie uma nova entrada para o evento
            try:
                # Extrair agenda da descrição do evento
                description = event.get('description', '')
                agenda = ""
                
                if description and "--- AGENDA ---" in description:
                    agenda = description.split("--- AGENDA ---")[1].strip()
                
                # Obter data e hora do evento
                start_datetime = None
                if 'dateTime' in event.get('start', {}):
                    start_datetime = datetime.fromisoformat(
                        event.get('start', {}).get('dateTime', '').replace('Z', '+00:00')
                    )
                
                # Obter data de criação do evento
                created_datetime = None
                if 'created' in event:
                    created_datetime = datetime.fromisoformat(
                        event.get('created', '').replace('Z', '+00:00')
                    )
                
                # Criar nova reunião
                new_meeting = Meeting(
                    title=event.get('summary', 'Reunião sem título'),
                    agenda=agenda if agenda else "Pauta não definida",
                    transcription="Esta reunião foi importada do Google Calendar e ainda não foi analisada.",
                    user_id=current_user.id,
                    meeting_date=start_datetime,
                    created_at=created_datetime,  # Usar a data de criação do evento do calendário
                    google_calendar_event_id=event_id
                )
                
                db.session.add(new_meeting)
                db.session.commit()
                
                event_to_meeting[event_id] = new_meeting.id
            except Exception as e:
                logger.error(f"Error syncing calendar event {event_id}: {str(e)}")
                continue
    
    return event_to_meeting

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
        
        # Build Google Calendar service
        service = build_service(credentials)
        
        # Sincroniza eventos do calendário com reuniões no banco
        sync_calendar_events_to_meetings(service)
        
        # Get a larger number of events including recent ones (past week and upcoming)
        events = list_upcoming_events(service, max_results=20, include_recent=True)
        
        # Obter data e hora atual no fuso horário de São Paulo para comparação no template
        # Usar o módulo pytz para lidar corretamente com o fuso horário
        import pytz
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        now = datetime.now(sao_paulo_tz).isoformat()
        
        # Verificar quais eventos já foram analisados
        # Cria uma lista de IDs de eventos do Google Calendar que já foram analisados
        analyzed_events = {}
        
        # Buscar as reuniões que possuem resultados (já foram analisadas)
        # Usamos uma consulta filtrada para buscar apenas as reuniões já analisadas
        from sqlalchemy import and_, or_, not_
        analyzed_meetings = Meeting.query.filter(
            Meeting.user_id == current_user.id,
            Meeting.results_json != None  # Forma mais compatível
        ).all()
        
        # Criar um dicionário mapeando os títulos de reuniões para seus IDs internos
        for meeting in analyzed_meetings:
            # Usa o título da reunião como chave de mapeamento
            # Não é perfeito, mas deve funcionar para a maioria dos casos
            analyzed_events[meeting.title] = meeting.id
        
        return render_template('calendar.html', 
                             events=events, 
                             current_time=now, 
                             analyzed_events=analyzed_events)
        
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

@app.route('/calendar/new', methods=['GET', 'POST'])
@login_required
def create_event():
    """Criar novo evento no Google Calendar"""
    if not current_user.google_calendar_enabled:
        flash("Você precisa conectar sua conta do Google Calendar primeiro.", "warning")
        return redirect(url_for('settings'))
        
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title')
            description = request.form.get('description', '')
            
            # Parse start datetime
            start_date = request.form.get('start_date')
            start_time = request.form.get('start_time')
            start_datetime = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
            
            # Parse end datetime
            end_date = request.form.get('end_date')
            end_time = request.form.get('end_time')
            end_datetime = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
            
            # Get attendees
            attendees = []
            if request.form.get('attendees'):
                attendees = [email.strip() for email in request.form.get('attendees').split(',') if email.strip()]
            
            # Get agenda
            agenda = request.form.get('agenda', '')
            
            # Add agenda to description if provided
            if agenda:
                full_description = f"{description}\n\n--- AGENDA ---\n{agenda}"
            else:
                full_description = description
            
            # Get user's Google credentials
            credentials = current_user.get_google_credentials()
            
            # Build Google Calendar service
            service = build_service(credentials)
            
            # Create event
            event = create_meeting_event(
                service=service,
                title=title,
                description=full_description,
                start_time=start_datetime,
                end_time=end_datetime,
                attendees=attendees
            )
            
            flash("Reunião agendada com sucesso!", "success")
            return redirect(url_for('event_details', event_id=event['id']))
            
        except Exception as e:
            logger.error(f"Error creating calendar event: {str(e)}")
            flash(f"Erro ao criar evento: {str(e)}", "danger")
            return redirect(url_for('create_event'))
            
    return render_template('create_event.html')
    
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
        meeting.language = results.get('language', 'auto')
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

