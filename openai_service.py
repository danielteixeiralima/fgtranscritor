from dotenv import load_dotenv
load_dotenv()

import os
import json
import logging
import tempfile
from openai import OpenAI

# the newest OpenAI model is "gpt-4o" which was released May 13, 2024.
# do not change this unless explicitly requested by the user
MODEL = "gpt-4o"
WHISPER_MODEL = "whisper-1"  # Modelo para transcrição de áudio

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_api_key = os.environ.get("OPENAI_API_KEY")
if not openai_api_key:
    logger.warning("OPENAI_API_KEY not found in environment variables")

client = OpenAI(api_key=openai_api_key)

def detect_language(text):
    """
    Detect the language of the provided text
    
    Args:
        text (str): Text to analyze
        
    Returns:
        str: Language code or 'en' if detection fails
    """
    try:
        logger.debug("Detecting language")
        
        # Keep prompt short to save tokens
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You detect the language of text. Respond with a language code only (e.g., 'en', 'pt', 'es', 'fr', etc.)"},
                {"role": "user", "content": f"Detect the language: {text[:200]}..."}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
        lang_code = response.choices[0].message.content.strip().lower()
        logger.debug(f"Detected language: {lang_code}")
        return lang_code
    
    except Exception as e:
        logger.error(f"Error detecting language: {str(e)}")
        return 'en'  # Default to English

def transcribe_audio(audio_file, max_file_size_mb=10):
    """
    Transcribe audio file using OpenAI Whisper API
    
    Args:
        audio_file: File-like object containing audio data
        max_file_size_mb: Tamanho máximo do arquivo em MB para processamento direto
        
    Returns:
        str: Transcription text
    """
    try:
        logger.debug("Starting audio transcription")
        
        # Logs detalhados para depuração
        logger.debug(f"Tipo de arquivo recebido: {type(audio_file)}")
        
        # Reset file pointer if needed
        if hasattr(audio_file, 'seek'):
            audio_file.seek(0)
        
        # Create a temporary file to save the audio data
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as temp_file:
                # Write audio data to the temporary file
                audio_data = audio_file.read()
                logger.debug(f"Tamanho dos dados de áudio lidos: {len(audio_data)} bytes")
                
                if len(audio_data) == 0:
                    raise Exception("Arquivo de áudio está vazio (0 bytes)")

                # Verificar se o arquivo é muito grande
                file_size_mb = len(audio_data) / (1024 * 1024)
                logger.debug(f"Tamanho do arquivo em MB: {file_size_mb:.2f}")
                
                if file_size_mb > max_file_size_mb:
                    logger.warning(f"Arquivo de áudio muito grande ({file_size_mb:.2f} MB). Reduzindo a qualidade para processamento.")
                    
                    # Para arquivos grandes, vamos usar uma amostragem menor para evitar timeout
                    # Opção 1: Usar apenas os primeiros X MB do áudio
                    max_bytes = max_file_size_mb * 1024 * 1024
                    logger.debug(f"Usando apenas os primeiros {max_file_size_mb} MB do áudio")
                    audio_data = audio_data[:max_bytes]
                    
                temp_file.write(audio_data)
                temp_file_path = temp_file.name
                logger.debug(f"Arquivo temporário criado: {temp_file_path}")
        except Exception as e:
            logger.error(f"Erro ao criar arquivo temporário: {str(e)}")
            raise
        
        # Open the file for the OpenAI API with timeout handling
        try:
            with open(temp_file_path, 'rb') as audio:
                # Call the OpenAI API for transcription with a shorter timeout
                logger.debug(f"Enviando arquivo para transcrição com a API OpenAI Whisper")
                
                # Definir um timeout mais curto para evitar bloqueio do worker
                # Usamos API de baixo nível (openai._base_client) para definir timeout de conexão e leitura
                # Infelizmente o parâmetro 'timeout' da API oficial não funciona adequadamente
                import openai._base_client
                oldtimeout = openai._base_client._DEFAULT_TIMEOUT
                try:
                    # Definir timeouts mais curtos para evitar que o worker fique bloqueado
                    # connect timeout, read timeout
                    openai._base_client._DEFAULT_TIMEOUT = (15.0, 20.0)
                    
                    response = client.audio.transcriptions.create(
                        model=WHISPER_MODEL,
                        file=audio
                    )
                finally:
                    # Restaurar o timeout original
                    openai._base_client._DEFAULT_TIMEOUT = oldtimeout
                logger.debug(f"Resposta da API recebida: {response}")
        except Exception as e:
            logger.error(f"Erro na chamada da API Whisper: {str(e)}")
            raise
        finally:
            # Clean up the temporary file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                    logger.debug(f"Arquivo temporário removido: {temp_file_path}")
                except Exception as e:
                    logger.warning(f"Não foi possível remover arquivo temporário: {str(e)}")
        
        # Log and return the transcription
        transcription_text = response.text
        logger.debug(f"Transcrição completa: {transcription_text[:100]}...")
        return transcription_text
        
    except Exception as e:
        logger.error(f"Error in audio transcription: {str(e)}")
        raise Exception(f"Failed to transcribe audio: {str(e)}")

def analyze_meeting(agenda, transcription, language=None, max_transcription_length=6000):
    """
    Analyze a meeting by comparing the agenda with the transcription
    
    Args:
        agenda (str): Meeting agenda/topics
        transcription (str): Full meeting transcription
        language (str, optional): Specific language to use for analysis, otherwise auto-detect
        max_transcription_length (int): Limite máximo de caracteres para a transcrição
        
    Returns:
        dict: Analysis results including agenda checklist, summary, insights and next steps
    """
    try:
        logger.debug("Starting meeting analysis")
        
        # Auto-detect language if not provided
        if not language or language == 'auto':
            # Combine a bit of agenda and transcription for better detection
            sample_text = agenda[:100] + " " + transcription[:100]
            language = detect_language(sample_text)
        
        logger.debug(f"Analysis will be performed in language: {language}")
        
        # Limitar o tamanho da transcrição para evitar timeout
        original_transcription_length = len(transcription)
        if len(transcription) > max_transcription_length:
            logger.warning(f"Transcrição muito longa ({len(transcription)} caracteres), limitando a {max_transcription_length} caracteres")
            # Usar primeiro terço e último terço para manter contexto do início e fim da reunião
            first_part = transcription[:max_transcription_length // 3]
            last_part = transcription[-(max_transcription_length // 3):]
            middle_length = max_transcription_length - len(first_part) - len(last_part) - 100
            middle_start = (original_transcription_length - middle_length) // 2
            middle_part = transcription[middle_start:middle_start+middle_length]
            
            transcription = f"{first_part}\n\n[...Parte da transcrição omitida para processamento...]\n\n{middle_part}\n\n[...Parte da transcrição omitida para processamento...]\n\n{last_part}"
            
            logger.debug(f"Transcrição resumida para {len(transcription)} caracteres")
        
        # Prepare the prompt for the OpenAI API
        system_message = f"""
        You are an expert meeting analyst. Your task is to analyze a meeting transcription 
        and determine if the agenda items were covered adequately.
        
        IMPORTANT: You must analyze and respond entirely in the same language as the agenda and transcription (detected as {language}).
        
        Provide your response in the following JSON format:
        {{
            "agenda_items": [
                {{
                    "item": "string - the agenda item",
                    "addressed": boolean - whether the item was addressed in the meeting,
                    "context": "string - brief excerpt or summary of how it was addressed (if applicable)"
                }}
            ],
            "unaddressed_items": [
                {{
                    "item": "string - the agenda item that wasn't addressed",
                    "recommendation": "string - recommendation for follow-up"
                }}
            ],
            "additional_topics": [
                "string - topics discussed that weren't in the agenda"
            ],
            "meeting_summary": "string - concise summary of the meeting (max 250 words)",
            "alignment_score": number - score from 1-10 of how well the meeting followed the agenda,
            "insights": [
                "string - 3-5 key insights about the meeting's alignment with the agenda"
            ],
            "next_steps": [
                "string - recommended next steps, especially for unaddressed items"
            ],
            "action_items": [
                "string - specific tasks generated during the meeting with clear assignees when mentioned"
            ],
            "directions": [
                "string - strategic directions, policies, or decisions established during the meeting"
            ],
            "language": "{language}" - include this field with the detected language code
        }}
        """
        
        user_message = f"""
        ### MEETING AGENDA:
        {agenda}
        
        ### MEETING TRANSCRIPTION:
        {transcription}
        
        Analyze the transcription against the agenda items to determine:
        1. Which agenda items were addressed and which were not
        2. What additional topics were discussed that weren't on the agenda
        3. How closely the meeting followed the agenda
        4. A brief summary of the meeting
        5. Key insights about the meeting's effectiveness
        6. Recommended next steps based on the meeting outcomes
        7. Action items with clear responsibilities mentioned during the meeting
        8. Strategic directions or decisions established during the meeting
        
        REMEMBER: Respond in the same language as the agenda and transcription ({language})
        """
        
        # Call the OpenAI API with timeout
        logger.debug("Sending request to OpenAI API")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
            timeout=60.0,  # Definir timeout de 60 segundos
            max_tokens=1500  # Limitar tamanho da resposta
        )
        
        # Parse the response
        results = json.loads(response.choices[0].message.content)
        logger.debug("Successfully received and parsed OpenAI API response")
        
        # Ensure the language field is included
        if 'language' not in results:
            results['language'] = language
            
        # Adicionar informação sobre truncamento, se aplicável
        if original_transcription_length > max_transcription_length:
            results['truncated_transcription'] = True
            results['original_length'] = original_transcription_length
            results['analysis_length'] = len(transcription)
            
            # Adicionar aviso nos insights
            if 'insights' in results and isinstance(results['insights'], list):
                results['insights'].append(
                    f"Nota: A transcrição original ({original_transcription_length} caracteres) foi truncada para análise. "
                    f"Partes do meio da reunião podem não ter sido completamente analisadas."
                )
            
        return results
        
    except Exception as e:
        logger.error(f"Error in analyze_meeting: {str(e)}")
        # Criar um resultado de fallback em caso de erro
        fallback_results = {
            "agenda_items": [{"item": item, "addressed": False, "context": "Não foi possível analisar devido a um erro"} 
                           for item in agenda.split('\n') if item.strip()],
            "unaddressed_items": [],
            "additional_topics": [],
            "meeting_summary": f"Não foi possível analisar a reunião devido a um erro: {str(e)}",
            "alignment_score": 0,
            "insights": ["Ocorreu um erro ao analisar a reunião. Por favor, tente novamente com uma transcrição menor."],
            "next_steps": ["Tentar novamente com uma transcrição menor ou dividir a análise em partes"],
            "action_items": [],
            "directions": [],
            "language": language or "pt",
            "error": str(e) 
        }
        return fallback_results

def generate_meeting_agenda(topic, description, language='pt'):
    """
    Gera automaticamente uma pauta de reunião com base em um tópico e descrição
    
    Args:
        topic (str): O tópico principal da reunião
        description (str): Uma breve descrição do que será discutido
        language (str): Idioma da pauta ('pt', 'en', etc.)
        
    Returns:
        dict: Um dicionário contendo o título e a pauta gerados
    """
    try:
        # Determinar o idioma para as instruções
        if language.lower() in ['pt', 'pt-br', 'portuguese']:
            system_prompt = """Você é um especialista em criar pautas de reunião efetivas.
            Gere uma pauta detalhada e bem estruturada para a reunião descrita.
            
            Sua resposta deve estar no formato JSON com os campos 'title' (um título atrativo para a reunião) e 'agenda'.
            
            O campo 'agenda' deve ser uma string em texto simples com o seguinte formato:
            - Use títulos numerados (1, 2, 3) para cada seção principal
            - Cada seção deve ter: título, objetivo claro e tempo estimado
            - Use formatação com quebras de linha para tornar a pauta legível
            - NÃO use markdown ou formatação avançada, apenas texto simples e quebras de linha
            - NÃO inclua caracteres especiais de JSON como colchetes ou chaves na própria pauta
            
            Use linguagem profissional e formal em português."""
        else:
            system_prompt = """You are an expert in creating effective meeting agendas.
            Generate a detailed and well-structured agenda for the described meeting.
            
            Your response should be in JSON format with fields 'title' (an attractive title for the meeting) and 'agenda'.
            
            The 'agenda' field should be a plain text string with the following format:
            - Use numbered titles (1, 2, 3) for each main section
            - Each section should have: title, clear objective, and estimated time
            - Use line breaks to make the agenda readable
            - DO NOT use markdown or advanced formatting, just plain text and line breaks
            - DO NOT include special JSON characters like brackets or braces in the agenda itself
            
            Use professional and formal language in English."""

        # Criar a mensagem para o usuário com os detalhes da reunião
        user_prompt = f"Tópico da reunião: {topic}\n\nDescrição: {description}"
        
        # Fazer a chamada para a API da OpenAI
        # o modelo mais recente da OpenAI é "gpt-4o" que foi lançado em 13 de maio de 2024
        # não mudar isso a menos que explicitamente solicitado pelo usuário
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        # Extrair e formatar o resultado
        content = response.choices[0].message.content
        agenda_data = json.loads(content)
        
        # Formatar a agenda para exibição legível, removendo qualquer formatação JSON bruta
        raw_agenda = agenda_data.get('agenda', '')
        
        # Garantir que a agenda esteja em formato de texto legível com quebras de linha apropriadas
        formatted_agenda = raw_agenda.replace('\\n', '\n').replace('\\', '')
        
        return {
            'title': agenda_data.get('title', topic),
            'agenda': formatted_agenda
        }
    
    except Exception as e:
        logger.error(f"Erro ao gerar pauta com OpenAI: {str(e)}")
        # Em caso de erro, retornar um formato básico com o tópico original
        return {
            'title': topic,
            'agenda': f"1. Introdução\n   - Objetivo: Dar boas-vindas e contextualizar a reunião\n   - Tempo: 5 minutos\n\n2. {description}\n   - Objetivo: Discutir o tema principal\n   - Tempo: 20 minutos\n\n3. Discussão aberta\n   - Objetivo: Coletar feedback e ideias\n   - Tempo: 15 minutos\n\n4. Próximos passos\n   - Objetivo: Definir ações e responsáveis\n   - Tempo: 10 minutos\n\n5. Encerramento\n   - Objetivo: Resumir decisões e agradecer participação\n   - Tempo: 5 minutos"
        }
