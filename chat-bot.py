# Version: Beta 1 (Gemini Telegram Bot - Assistant IA Système)
import os, time, subprocess, logging, shlex, re, random, asyncio, json
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict, deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from dotenv import load_dotenv
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from google.api_core import exceptions as google_exceptions

# ============================= CONFIGURATION =============================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
base_dir = Path(__file__).resolve().parent
load_dotenv(dotenv_path=base_dir / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", 0))

os.environ['TZ'] = 'Europe/Paris'
try: time.tzset()
except: pass

LAST_SEEN_FILE = base_dir / ".last_seen"
AUDIT_LOG_FILE = base_dir / ".audit_log.jsonl"
COMMAND_TIMEOUT = 90  # Timeout configurable

# Rate limiting pour tests : max 30 requêtes par minute par utilisateur
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW = 60  # secondes

# Système de détection de surcharge API (variables déclarées dans les fonctions)
# Variables globales pour la gestion des surcharges API
api_overload_detected = False
last_api_error_time = 0
user_request_times = defaultdict(deque)

# Sessions par utilisateur (fallback sur session globale)
user_sessions = {}
global_chat_session = None

# Historique de conversation par utilisateur pour le contexte
user_conversation_history = defaultdict(list)  # user_id -> list of (timestamp, user_message, bot_response)
MAX_HISTORY_LENGTH = 20  # Garder les 20 derniers échanges

# Système de rotation FORCÉ pour garantir la variété
user_reaction_counters = defaultdict(lambda: defaultdict(int))  # Compteur par catégorie par utilisateur
user_last_reactions = defaultdict(list)  # Garde les 30 dernières réactions par utilisateur (augmenté pour plus de variété)
user_category_history = defaultdict(lambda: defaultdict(list))  # Historique par catégorie pour rotation intelligente

REACTION_MAP = {
    'love': ['❤️', '✨', '🤝', '🥰', '💖', '🌟', '🎩', '🥂', '🫶', '💝', '💕', '🌺', '🎁', '🌈'],
    'fun': ['😄', '🚀', '😎', '👻', '🥳', '🤪', '🎉', '🎈', '🤠', '😏', '🎪', '🎭', '🎨', '🎯'],
    'motivation': ['🏆', '🎯', '🦁', '🔥', '💪', '⚡', '🎖️', '⭐', '🌟', '💫', '🚀', '💎', '👑', '🎖️'],
    'tech': ['⚙️', '🖥️', '🛡️', '💾', '🔧', '🛠️', '📡', '🔌', '💻', '🖱️', '⌨️', '🖨️', '🔬', '🧪', '📊', '🔍', '🎛️', '⚡'],
    'error': ['⚠️', '🚫', '🔧', '🚑', '💀', '🚨', '🚒', '🩹', '🚧', '📛', '❌', '🔴', '⛔', '🛑'],
    'success': ['✅', '🎉', '✨', '🔥', '💯', '🏅', '🎊', '👏', '🙌', '🎈', '🌟', '💎', '👑', '🎁'],
    'neutral': ['✅', '🫡', '👀', '👌', '👍', '📝', '📋', '📂', '☕', '🧘', '🤔', '💭', '🔍', '📌', '📍', '💡', '🔔', '📢']
}

# ============================= UTILITAIRES =============================

def audit_log(user_id: int, action: str, command: str = "", result: str = "", success: bool = True):
    """Log d'audit complet pour traçabilité."""
    try:
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "action": action,
            "command": command[:500] if command else "",  # Limiter la taille
            "result_preview": result[:200] if result else "",
            "success": success
        }
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"Erreur audit log: {e}")

def check_rate_limit(user_id: int) -> bool:
    """Vérifie si l'utilisateur dépasse la limite de taux."""
    now = time.time()
    times = user_request_times[user_id]
    
    # Nettoyer les requêtes anciennes
    while times and times[0] < now - RATE_LIMIT_WINDOW:
        times.popleft()
    
    if len(times) >= RATE_LIMIT_REQUESTS:
        return False
    
    times.append(now)
    return True

def get_user_session(user_id: int):
    """Récupère ou crée une session pour l'utilisateur."""
    global global_chat_session, model
    if model is None:
        raise Exception("Modèle Gemini non initialisé. Vérifiez GEMINI_API_KEY.")
    if user_id not in user_sessions:
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
    return user_sessions.get(user_id, global_chat_session)

def add_to_conversation_history(user_id: int, user_message: str, bot_response: str = ""):
    """Ajoute un échange à l'historique de conversation."""
    timestamp = datetime.now().isoformat()
    user_conversation_history[user_id].append({
        'timestamp': timestamp,
        'user_message': user_message[:500],  # Limiter la taille
        'bot_response': bot_response[:500] if bot_response else ""
    })

    # Garder seulement les derniers échanges
    if len(user_conversation_history[user_id]) > MAX_HISTORY_LENGTH:
        user_conversation_history[user_id] = user_conversation_history[user_id][-MAX_HISTORY_LENGTH:]

def get_conversation_context(user_id: int, reply_to_message=None) -> str:
    """Construit le contexte de conversation pour le prompt."""
    context_parts = []

    # Si c'est une réponse à un message spécifique
    if reply_to_message and hasattr(reply_to_message, 'text') and reply_to_message.text:
        context_parts.append(f"CONTEXTE DE LA CONVERSATION PRÉCÉDENTE:")
        context_parts.append(f"Message précédent: {reply_to_message.text}")
        context_parts.append("")

    # Ajouter l'historique récent (3-5 derniers échanges)
    history = user_conversation_history.get(user_id, [])
    if len(history) > 0:
        recent_history = history[-5:]  # 5 derniers échanges
        context_parts.append("HISTORIQUE RÉCENT DE LA CONVERSATION:")
        for i, exchange in enumerate(recent_history):
            context_parts.append(f"Échange {i+1}:")
            context_parts.append(f"  Utilisateur: {exchange['user_message']}")
            if exchange['bot_response']:
                context_parts.append(f"  Bot: {exchange['bot_response']}")
        context_parts.append("")

    return "\n".join(context_parts) if context_parts else ""

def escape_sed_pattern(pattern: str) -> str:
    """Échappe correctement un pattern pour sed."""
    return pattern.replace('\\', '\\\\').replace('/', '\\/').replace('&', '\\&')

def validate_file_path(path: str) -> bool:
    """Valide qu'un chemin de fichier est sûr."""
    if not path or len(path) > 500:
        return False
    # Empêcher les chemins relatifs dangereux
    if '..' in path or path.startswith('/root') or '/etc/shadow' in path:
        return False
    return True

# ============================= OUTILS =============================

def run_terminal_command(command: str = None, **kwargs):
    if not command: return "Erreur : Commande vide."
    
    # 1. FIREWALL FICHIERS SENSIBLES (amélioré)
    sensitive_patterns = ['.env', 'id_rsa', 'shadow', 'passwd', 'authorized_keys', 'private.key', 
                         '/root/.ssh', '/etc/shadow', '/etc/passwd']
    if any(x in command.lower() for x in sensitive_patterns):
        return "⛔ SÉCURITÉ : Accès refusé à ce fichier sensible."

    try:
        # 2. AUTO-SUDO INTELLIGENT
        admin_cmds = ['apt', 'systemctl', 'fail2ban', 'netstat', 'ss', 'ls /root', 'ufw', 'crontab', 'rm', 'mv', 'reboot', 'df', 'free', 'top', 'htop']
        if any(c in command for c in admin_cmds) and 'sudo' not in command:
            command = f"sudo -n {command}" # -n pour non-interactif

        logging.info(f"EXEC : {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        
        output = (result.stdout + result.stderr).strip()
        return output[:3800] if output else "✅ Commande exécutée (aucun retour visuel)."
    except subprocess.TimeoutExpired:
        return f"❌ Timeout : Commande annulée après {COMMAND_TIMEOUT}s."
    except Exception as e:
        return f"❌ Erreur d'exécution : {str(e)}"

def modify_system_file(path: str, action: str, content: str = "", pattern: str = "", line: int = 1, owner: str = "", group: str = "", permissions: str = ""):
    # Validation du chemin
    if not validate_file_path(path):
        return "⛔ SÉCURITÉ : Chemin de fichier invalide ou dangereux."
    
    if any(x in path for x in ['.env', 'id_rsa', 'shadow', '/root/.ssh', '/etc/shadow']): 
        return "⛔ SÉCURITÉ : Fichier protégé."
    
    try:
        safe_path = shlex.quote(path)
        safe_content = shlex.quote(content)
        
        # Détection automatique des permissions pour /var/www/
        auto_owner = owner
        auto_group = group
        auto_perms = permissions
        
        if '/var/www/' in path:
            # Pour les fichiers web, utiliser www-data par défaut
            if not auto_owner:
                # Récupérer l'utilisateur actuel
                try:
                    result = subprocess.run("whoami", shell=True, capture_output=True, text=True, timeout=5)
                    auto_owner = result.stdout.strip() if result.returncode == 0 else ""
                except:
                    auto_owner = ""
            if not auto_group:
                auto_group = "www-data"
            if not auto_perms:
                auto_perms = "644"  # rw-r--r-- pour les fichiers
        
        # Créer le répertoire parent s'il n'existe pas
        parent_dir = str(Path(path).parent)
        if parent_dir and parent_dir != path:
            try:
                # Vérifier si le répertoire existe
                check_cmd = f"test -d {shlex.quote(parent_dir)}"
                result = subprocess.run(check_cmd, shell=True, timeout=5)
                if result.returncode != 0:
                    # Le répertoire n'existe pas, le créer
                    subprocess.run(f"sudo mkdir -p {shlex.quote(parent_dir)}", shell=True, check=True, timeout=10)
                    logging.info(f"Répertoire créé : {parent_dir}")
                
                # Permissions du répertoire si dans /var/www/
                if '/var/www/' in parent_dir:
                    subprocess.run(f"sudo chmod 755 {shlex.quote(parent_dir)}", shell=True, check=False, timeout=5)
                    if auto_group and auto_owner:
                        subprocess.run(f"sudo chown {auto_owner}:{auto_group} {shlex.quote(parent_dir)}", shell=True, check=False, timeout=5)
                    elif auto_group:
                        # Récupérer le propriétaire actuel
                        result = subprocess.run(f"stat -c '%U' {shlex.quote(parent_dir)}", shell=True, capture_output=True, text=True, timeout=5)
                        current_owner = result.stdout.strip() if result.returncode == 0 else ""
                        if current_owner:
                            subprocess.run(f"sudo chown {current_owner}:{auto_group} {shlex.quote(parent_dir)}", shell=True, check=False, timeout=5)
            except subprocess.CalledProcessError as e:
                logging.warning(f"Impossible de créer/configurer le répertoire {parent_dir}: {e}")
            except Exception as e:
                logging.warning(f"Erreur lors de la création du répertoire {parent_dir}: {e}")
        
        # Exécuter l'action principale
        # TOUJOURS utiliser un fichier temporaire pour garantir la fiabilité (surtout pour contenu multi-lignes)
        import tempfile
        use_temp_file = True  # Toujours utiliser un fichier temporaire pour plus de fiabilité
        
        if action == "append": 
            if use_temp_file or len(content) > 100 or '\n' in content or '"' in content or "'" in content or '{' in content or '}' in content:
                # Utiliser un fichier temporaire pour éviter les problèmes d'échappement
                with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    # Utiliser cat + tee pour préserver le contenu exact
                    result = subprocess.run(
                        f"sudo bash -c 'cat {shlex.quote(tmp_path)} >> {safe_path}'",
                        shell=True, check=True, timeout=30, capture_output=True, text=True
                    )
                except subprocess.CalledProcessError as e:
                    return f"❌ Erreur lors de l'ajout au fichier : {str(e)}. Sortie: {e.stderr}"
                finally:
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
            else:
                cmd = f"echo {safe_content} | sudo tee -a {safe_path} > /dev/null"
                subprocess.run(cmd, shell=True, check=True, timeout=30)
        elif action == "overwrite": 
            # TOUJOURS utiliser un fichier temporaire pour overwrite (plus fiable)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                # Utiliser cat + tee pour créer/écraser le fichier avec le bon contenu
                result = subprocess.run(
                    f"sudo bash -c 'cat {shlex.quote(tmp_path)} > {safe_path}'",
                    shell=True, check=True, timeout=30, capture_output=True, text=True
                )
            except subprocess.CalledProcessError as e:
                return f"❌ Erreur lors de l'écriture du fichier : {str(e)}. Sortie: {e.stderr}"
            finally:
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        elif action == "replace" and pattern:
            # Échappement sécurisé pour sed
            escaped_pattern = escape_sed_pattern(pattern)
            escaped_replacement = escape_sed_pattern(content)
            cmd = f"sudo sed -i 's/{escaped_pattern}/{escaped_replacement}/g' {safe_path}"
            subprocess.run(cmd, shell=True, check=True, timeout=30)
        elif action == "remove":
            if not isinstance(line, int) or line < 1:
                return "❌ Numéro de ligne invalide."
            cmd = f"sudo sed -i '{line}d' {safe_path}"
            subprocess.run(cmd, shell=True, check=True, timeout=30)
        else: 
            return "Action inconnue."
        
        # Vérifier que le fichier existe bien après l'opération
        verify_cmd = f"test -f {safe_path}"
        verify_result = subprocess.run(verify_cmd, shell=True, timeout=5)
        if verify_result.returncode != 0:
            return f"❌ Le fichier {path} n'a pas pu être créé/vérifié."
        
        # Appliquer les permissions et propriétaire après création/modification
        perms_applied = []
        if auto_perms or auto_owner or auto_group:
            perms_cmd = []
            if auto_perms:
                perms_cmd.append(("chmod", f"sudo chmod {auto_perms} {safe_path}"))
            if auto_owner and auto_group:
                perms_cmd.append(("chown", f"sudo chown {auto_owner}:{auto_group} {safe_path}"))
            elif auto_owner:
                perms_cmd.append(("chown", f"sudo chown {auto_owner} {safe_path}"))
            elif auto_group:
                # Si seulement le groupe est spécifié, garder le propriétaire actuel
                result = subprocess.run(f"stat -c '%U' {safe_path}", shell=True, capture_output=True, text=True, timeout=5)
                current_owner = result.stdout.strip() if result.returncode == 0 else ""
                if current_owner:
                    perms_cmd.append(("chown", f"sudo chown {current_owner}:{auto_group} {safe_path}"))
            
            for perm_type, chmod_cmd in perms_cmd:
                try:
                    subprocess.run(chmod_cmd, shell=True, check=True, timeout=10)
                    perms_applied.append(perm_type)
                except Exception as e:
                    logging.warning(f"Impossible d'appliquer {perm_type} sur {path}: {e}")
        
        # Message de retour avec info permissions
        msg = f"✅ Fichier {path} modifié."
        if perms_applied:
            perm_info = []
            if auto_perms and "chmod" in perms_applied:
                perm_info.append(f"permissions {auto_perms}")
            if auto_owner and auto_group and "chown" in perms_applied:
                perm_info.append(f"propriétaire {auto_owner}:{auto_group}")
            elif auto_group and "chown" in perms_applied:
                perm_info.append(f"groupe {auto_group}")
            if perm_info:
                msg += f" ({', '.join(perm_info)})"
        
        return msg
    except subprocess.TimeoutExpired:
        return f"❌ Timeout : Modification annulée après 30s."
    except subprocess.CalledProcessError as e:
        error_detail = e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)
        logging.error(f"Erreur modification fichier {path}: {error_detail}")
        return f"❌ Erreur fichier : {error_detail[:200]}"
    except Exception as e: 
        logging.error(f"Exception modification fichier {path}: {e}", exc_info=True)
        return f"❌ Erreur fichier : {str(e)[:200]}"

my_tools = [run_terminal_command, modify_system_file]

# ============================= COEUR IA =============================
model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        
        system_instruction = """
    TU ES LE SYSTEME D'EXPLOITATION INCARNÉ. TU ES UN INVESTIGATEUR EXPERT DU SERVEUR.
    
    >>> RÈGLE ABSOLUE #1 : CONCISION ET EFFICACITÉ <<<
    - SOIS DIRECT : Réponds simplement et clairement, pas de phrases inutiles
    - Pour les questions SIMPLES : Pas besoin de lancer 10 commandes, utilise le bon outil du premier coup
    - Pour les questions COMPLEXES : Investigate systématiquement, mais résume la réponse
    - INTERDICTION des réponses verbeuses pour des trucs évidents
    - EXEMPLE À SUIVRE : "top" → "Charge CPU: 15%" (pas de paragraphe d'explication)

    >>> PROTOCOLE D'INVESTIGATION FORCÉE (À APPLIQUER TOUJOURS) <<<
    
    Pour identifier un SERVICE/PROCESSUS :
    1. Ports et connexions : `ss -tulpn`, `lsof -i`, `netstat -tulpn`, `ss -s`
    2. Services système : `systemctl list-units --all --type=service`, `systemctl list-units --state=running`
    3. Processus actifs : `ps aux`, `ps auxf`, `pgrep -a [mot]`, `pstree -p`
    4. Logs système : `journalctl -xe --no-pager | tail -100`, `journalctl -u [service] --no-pager`
    5. Logs fichiers : `tail -50 /var/log/syslog`, `tail -50 /var/log/messages`, `dmesg | tail -50`
    6. Configurations : `grep -r [mot] /etc/ 2>/dev/null | head -20`, `find /etc -name "*[mot]*" 2>/dev/null`
    7. Répertoires système : `ls -la /var/run/`, `ls -la /run/`, `ls -la /tmp/ | grep [mot]`
    8. Variables d'environnement : `env | grep -i [mot]`, `systemctl show [service] | grep -i [mot]`
    
    Pour identifier un PORT :
    1. Écoute : `ss -tulpn | grep [port]`, `lsof -i :[port]`, `netstat -tulpn | grep [port]`
    2. Services : `cat /etc/services | grep [port]`, `grep [port] /etc/services`
    3. Processus : `fuser [port]/tcp`, `fuser [port]/udp`
    4. Connexions : `ss -an | grep [port]`, `netstat -an | grep [port]`
    
    Pour identifier un PROBLÈME/ERREUR :
    1. Logs récents : `journalctl -p err -xe --no-pager | tail -50`, `journalctl --since "10 minutes ago" --no-pager`
    2. Logs fichiers : `tail -100 /var/log/syslog | grep -i error`, `tail -100 /var/log/messages | grep -i error`
    3. Système : `dmesg | grep -i error | tail -20`, `systemctl --failed`
    4. Disque : `df -h`, `du -sh /* 2>/dev/null | sort -h | tail -10`
    5. Mémoire : `free -h`, `vmstat 1 3`
    6. CPU : `top -b -n 1 | head -20`, `ps aux --sort=-%cpu | head -10`
    
    >>> RÈGLE ABSOLUE #2 : INTERDICTION DE DEMANDER À L'UTILISATEUR <<<
    - Ne dis JAMAIS : "Peux-tu lancer cette commande ?" ou "Voici la commande à taper".
    - SI TU CONNAIS LA COMMANDE, TAPE-LA TOI-MÊME VIA `run_terminal_command`.
    - Si le résultat est vide ou "not found", LANCE IMMÉDIATEMENT d'autres commandes de recherche.
    
    >>> STRATÉGIE D'INVESTIGATION EN CASCADE <<<
    Si la première commande ne donne rien :
    1. Essaie une variante (grep au lieu de find, ss au lieu de netstat)
    2. Élargis la recherche (supprime les filtres, cherche dans plus de répertoires)
    3. Change d'angle (cherche dans les logs au lieu des processus, dans les configs au lieu des services)
    4. Combine les résultats de plusieurs commandes pour reconstruire l'information
    5. Si TOUJOURS rien : cherche des indices indirects (processus liés, fichiers de config, etc.)

    >>> QUESTIONS QUI SEMBLENT SIMPLES MAIS NÉCESSITENT INVESTIGATION <<<
    Même pour ces questions "basiques", tu dois INVESTIGUER :
    - "Ça marche ?" → Vérifier services, logs, connexions, ressources
    - "Qu'est-ce qui ne va pas ?" → Scanner tous les logs d'erreur systématiquement
    - "Le serveur fonctionne ?" → Vérifier load, mémoire, disque, services critiques
    - "Y a-t-il un problème ?" → Audit complet : logs + services + ressources + réseau
    - "Comment ça va ?" → Rapport détaillé du statut système
    
    >>> EXEMPLES CONCRETS D'INVESTIGATION FORCÉE <<<

    Question simple : "Le serveur est-il allumé ?"
    Actions OBLIGATOIRES (même si ça semble évident) :
    - `uptime`
    - `systemctl is-system-running`
    - `ps aux | head -10`
    - `df -h`
    - `free -h`
    - `ss -s`
    - `systemctl --failed`

    Question : "Quel service écoute sur le port 8080 ?"
    Actions SYSTÉMATIQUES (en séquence rapide) :
    - `ss -tulpn | grep 8080`
    - `lsof -i :8080`
    - `fuser 8080/tcp`
    - `systemctl list-units --all | grep -i 8080`
    - `ps aux | grep -i 8080`
    - `journalctl -xe --no-pager | grep -i 8080 | tail -20`
    - `cat /etc/services | grep 8080`
    - `netstat -tulpn | grep 8080` (si ss échoue)
    - `grep -r "8080" /etc/ 2>/dev/null | head -10`

    Question simple : "Y a-t-il des erreurs dans les logs ?"
    Actions COMPLÈTES (pas de réponse courte) :
    - `journalctl -p err -xe --no-pager | tail -20`
    - `tail -50 /var/log/syslog | grep -i error`
    - `tail -50 /var/log/messages | grep -i error`
    - `dmesg | grep -i error | tail -10`
    - `systemctl --failed`
    - `journalctl --since "1 hour ago" --no-pager | grep -i error | tail -10`
    
    Question : "Quel service fait X ?"
    Actions IMMÉDIATES :
    - `ps aux | grep -i X`
    - `systemctl list-units --all | grep -i X`
    - `pgrep -a -i X`
    - `journalctl -xe --no-pager | grep -i X | tail -30`
    - `grep -r -i X /etc/ 2>/dev/null | head -20`
    - `find /etc -name "*X*" 2>/dev/null`
    - `systemctl list-units --type=service | grep -i X`
    
    Question : "Pourquoi le serveur est lent ?"
    Actions IMMÉDIATES :
    - `top -b -n 1 | head -30`
    - `ps aux --sort=-%cpu | head -15`
    - `ps aux --sort=-%mem | head -15`
    - `df -h`
    - `free -h`
    - `iostat -x 1 2`
    - `ss -s`
    - `systemctl --failed`
    - `journalctl -p err -xe --no-pager | tail -30`
    
    >>> CRÉATION ET MODIFICATION DE FICHIERS <<<
    - TU PEUX CRÉER ET MODIFIER DES FICHIERS SANS PROBLÈME via `modify_system_file`.
    - Pour créer un fichier : utilise `action="overwrite"` avec le `content` complet (peut contenir plusieurs lignes, CSS, HTML, etc.).
    - Pour ajouter du contenu : utilise `action="append"`.
    - Le système gère AUTOMATIQUEMENT :
      * La création des répertoires parents si nécessaire
      * Les permissions (644 pour fichiers, 755 pour répertoires)
      * Le propriétaire et groupe (www-data pour /var/www/)
    - TU PEUX CRÉER DES FICHIERS CSS, HTML, CONFIG avec du contenu multi-lignes SANS PROBLÈME.
    - Exemple : Pour créer style.css dans /var/www/tristan.louloutech.fr/, utilise :
      modify_system_file(path="/var/www/tristan.louloutech.fr/style.css", action="overwrite", content="body { font-family: sans-serif; }")
    - NE DIS JAMAIS "je ne peux pas créer le fichier" - TU PEUX LE FAIRE via modify_system_file.
    
    >>> SÉCURITÉ <<<
    - Suppression (rm) / Modif fichier -> ATTENDS LA CONFIRMATION.
    - Tout le reste (Info, Install, Update, Recherche, Création fichier) -> EXÉCUTION IMMÉDIATE.
    - Utilise toujours `-y` pour les installations.
    
    >>> STYLE DE RÉPONSE <<<
    - SOIS CONCIS ET DIRECT : Pas de blabla inutile, va droit au but !
    - Pour les questions simples : Réponds simplement sans expliquer chaque étape
    - Pour les commandes système : Montre juste le résultat utile, pas les détails techniques
    - EXEMPLE : Question "Quelle heure ?" → Réponse "Il est 14h30"
    - EXEMPLE : Commande "ls" → Montre la liste, pas "J'exécute ls qui liste..."

    >>> TAGS DE RÉACTION <<<
    - TOUJOURS ajouter un tag à la fin : [R:TECH], [R:SUCCESS], [R:NEUTRAL], [R:LOVE], [R:MOTIVATION], [R:FUN]
    - Utilise [R:SUCCESS] quand tu fournis une réponse utile
    - Utilise [R:TECH] pour les résultats techniques
    - Utilise [R:NEUTRAL] pour les réponses basiques

    >>> MODE MORNING <<<
    - Si tu vois [MORNING] dans le prompt, commence par une phrase de motivation naturelle
    - Puis traite la demande normalement et concrètement
        """

        # Test des modèles par ordre de préférence (du moins restrictif au plus restrictif)
        # gemini-2.5-flash-lite a été testé et fonctionne mieux que les autres
        preferred_models = [
            "gemini-2.5-flash-lite",  # ⭐ TESTÉ ET RECOMMANDÉ - Meilleures performances
            "gemini-flash-lite-latest",  # Dernière version lite
            "gemini-2.0-flash-lite",  # Version lite 2.0
            "gemini-2.5-flash",  # Nouvelle version stable
            "gemini-flash-latest",  # Dernière version Flash
            "gemini-2.0-flash"  # Modèle actuel (fallback)
        ]

        model = None
        for model_name in preferred_models:
            try:
                logging.info(f"Tentative d'initialisation du modèle: {model_name}")
                test_model = genai.GenerativeModel(
                    model_name=model_name,
                    tools=my_tools,
                    system_instruction=system_instruction,
                    safety_settings={HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE}
                )
                # Test rapide du modèle
                test_model.generate_content("test", generation_config={"max_output_tokens": 5})
                model = test_model
                logging.info(f"✅ Modèle {model_name} sélectionné avec succès")
                break
            except Exception as e:
                logging.warning(f"❌ Modèle {model_name} non disponible: {e}")
                continue

        if model is None:
            raise Exception("Aucun modèle Gemini n'est disponible. Vérifiez votre quota API.")
        global_chat_session = model.start_chat(enable_automatic_function_calling=True)
    except Exception as e:
        logging.error(f"Erreur initialisation Gemini: {e}")
        model = None
        global_chat_session = None

# ============================= LOGIQUE & SÉCURITÉ =============================

async def call_gemini_with_retry(session, prompt: str, max_retries: int = 8, user_id: int = 0):
    """Appel Gemini avec retry automatique ultra-renforcé pour gérer les quotas stricts."""
    base_wait = 2.0  # Temps d'attente de base plus long

    for attempt in range(max_retries):
        try:
            # send_message est synchrone, on l'exécute dans un thread pour ne pas bloquer
            return await asyncio.to_thread(session.send_message, prompt)
        except google_exceptions.ResourceExhausted:
            if attempt == max_retries - 1:
                logging.error(f"Rate limit définitif après {max_retries} tentatives pour user {user_id}")
                raise
            # Backoff plus agressif : attendre beaucoup plus longtemps entre les tentatives
            wait_time = base_wait * (2.5 ** attempt) + random.uniform(1.0, 3.0)
            logging.info(f"🤖 API quota atteint - tentative {attempt + 1}/{max_retries}, attente {wait_time:.1f}s...")
            await asyncio.sleep(wait_time)
        except google_exceptions.ServiceUnavailable:
            if attempt == max_retries - 1:
                logging.error(f"Service indisponible définitif après {max_retries} tentatives pour user {user_id}")
                raise
            # Pour les indisponibilités de service, attendre plus longtemps
            wait_time = base_wait * (3 ** attempt) + random.uniform(1.0, 3.0)
            logging.info(f"🤖 Service indisponible - tentative {attempt + 1}/{max_retries}, attente {wait_time:.1f}s...")
            await asyncio.sleep(wait_time)
        except google_exceptions.InvalidArgument as e:
            # Erreur de paramètre - pas de retry
            logging.error(f"Erreur de paramètre API (pas de retry): {e}")
            raise
        except Exception as e:
            # Autres erreurs - retry avec backoff standard
            if attempt == max_retries - 1:
                logging.error(f"Erreur API fatale après {max_retries} tentatives: {e}")
                raise
            wait_time = base_wait * (1.5 ** attempt) + random.uniform(0.2, 1.0)
            logging.warning(f"Erreur API inattendue - tentative {attempt + 1}/{max_retries}, attente {wait_time:.1f}s: {e}")
            await asyncio.sleep(wait_time)
    return None

def escape_markdown_v2(text: str) -> str:
    """Échappe les caractères spéciaux pour Markdown V2."""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

# Envoi sécurisé pour éviter le crash Markdown
async def safe_reply(update, text, markup=None, as_code=False):
    """Envoi de message avec gestion robuste des erreurs Markdown."""
    try:
        if as_code:
            # Pour Markdown V2, on doit échapper le contenu du code block
            escaped_text = escape_markdown_v2(text)
            await update.message.reply_text(
                f"```\n{escaped_text}\n```", 
                parse_mode=constants.ParseMode.MARKDOWN_V2, 
                reply_markup=markup
            )
        else:
            # Markdown standard pour le texte normal
            await update.message.reply_text(
                text, 
                parse_mode=constants.ParseMode.MARKDOWN, 
                reply_markup=markup
            )
    except Exception as e:
        # Fallback : texte brut sans formatage
        logging.warning(f"Erreur Markdown, fallback texte brut: {e}")
        clean = text.replace('`', '').replace('*', '').replace('_', '').replace('[', '').replace(']', '')
        try:
            await update.message.reply_text(clean[:4096], reply_markup=markup)  # Limite Telegram
        except Exception as e2:
            logging.error(f"Erreur même en fallback: {e2}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Vérification utilisateur autorisé
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    # Vérification surcharge API globale (cooldown court pour tests)
    global api_overload_detected, last_api_error_time
    if api_overload_detected:
        time_since_error = time.time() - last_api_error_time
        if time_since_error < 30:  # 30 secondes de cooldown seulement
            # Ignorer temporairement pour éviter spam
            audit_log(user_id, "MESSAGE_IGNORED", command="API_OVERLOAD_ACTIVE", success=False)
            return
        else:
            # Réactiver après 30 secondes
            api_overload_detected = False
            logging.info("Mode surcharge API terminé, reprise normale")

    # Rate limiting renforcé
    if not check_rate_limit(user_id):
        await update.message.reply_text("⚠️ Trop de requêtes. Attendez quelques secondes.")
        # Appliquer une réaction d'avertissement
        try:
            await update.message.set_reaction('⚠️')
        except:
            try:
                await update.message.set_reaction('🚫')
            except:
                pass
        return
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Morning Logic - Message personnalisé de motivation
    today = str(date.today())
    prefix = ""
    is_morning = False
    try:
        if not LAST_SEEN_FILE.exists() or open(LAST_SEEN_FILE).read().strip() != today:
            is_morning = True
            # Phrases de motivation variées
            morning_phrases = [
                "🌅 Bonjour ! Nouvelle journée, nouvelles opportunités. Prêt à conquérir le serveur ?",
                "☀️ Salut ! Le serveur t'attend, et moi aussi. On y va ?",
                "🚀 Hey ! Nouveau jour, nouvelle énergie. Que veux-tu accomplir aujourd'hui ?",
                "💪 Bonjour ! Le serveur est prêt, et toi aussi. C'est parti !",
                "✨ Salut ! Une nouvelle journée commence. Prêt à faire des choses géniales ?",
                "🔥 Bonjour ! Le serveur tourne, l'IA est prête. Qu'est-ce qu'on fait ?",
                "🎯 Hey ! Nouveau jour, nouveaux défis. Je suis là pour t'aider !",
                "⚡ Salut ! L'énergie est là, le serveur aussi. On commence ?"
            ]
            selected_phrase = random.choice(morning_phrases)
            prefix = f"[MORNING] {selected_phrase}\n\n"
        with open(LAST_SEEN_FILE, "w") as f: f.write(today)
    except Exception as e:
        logging.warning(f"Erreur lecture last_seen: {e}")

    # CONSTRUIRE LE CONTEXTE DE CONVERSATION
    conversation_context = get_conversation_context(user_id, getattr(update.message, 'reply_to_message', None))

    # INJECTION DE CONCISION : Réponses directes et efficaces
    system_injection = """
[SYSTEM: SOIS CONCIS ET DIRECT]
- Réponds simplement à la question posée
- Pas de blabla inutile, va droit au but
- Utilise les outils quand nécessaire, mais résume les résultats
- Pour les questions simples : réponse directe
- Pour les questions complexes : investigate puis résume clairement
[SYSTEM: UTILISE TES OUTILS SI NÉCESSAIRE, MAIS RÉPONDS SIMPLEMENT]
"""

    # Construire le prompt final avec contexte
    prompt_parts = []
    if prefix:
        prompt_parts.append(prefix)
    if conversation_context:
        prompt_parts.append(conversation_context)
    prompt_parts.append(update.message.text)
    prompt_parts.append(system_injection)

    final_prompt = "\n".join(prompt_parts)

    # Audit log de la requête
    audit_log(user_id, "MESSAGE_RECEIVED", command=update.message.text[:200])

    try:
        # Utiliser la session de l'utilisateur (ou globale en fallback)
        session = get_user_session(user_id)
        response = await call_gemini_with_retry(session, final_prompt, user_id=user_id)

        # Sauvegarder le message utilisateur dans l'historique
        add_to_conversation_history(user_id, update.message.text)
        
        # --- DÉTECTION OUTIL (API UPDATE) ---
        function_call = None
        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_call = part.function_call
                break
        
        if function_call:
            func_name = function_call.name
            args = dict(function_call.args)
            cmd_content = args.get('command', '') or args.get('path', '')

            # --- SÉCURITÉ : ANALYSE DE LA COMMANDE ---
            is_critical = False
            
            # 1. Modification fichier -> Critique
            if func_name == 'modify_system_file': 
                is_critical = True
            
            # 2. Shell : rm, mv, reboot, shutdown -> Critique
            if func_name == 'run_terminal_command':
                if re.search(r'\b(rm\s|mv\s|reboot|shutdown|mkfs|dd\s|format)\b', cmd_content, re.IGNORECASE):
                    is_critical = True

            if is_critical:
                context.user_data['pending'] = {"name": func_name, "args": args}
                kb = [[InlineKeyboardButton("✅ OUI", callback_data="yes"), InlineKeyboardButton("❌ NON", callback_data="no")]]
                confirm_msg = f"⚠️ **CONFIRMATION REQUISE**\nCommande : `{cmd_content}`"
                await safe_reply(update, confirm_msg, InlineKeyboardMarkup(kb))
                # Appliquer une réaction d'erreur/avertissement
                try:
                    await update.message.set_reaction('⚠️')
                except:
                    try:
                        await update.message.set_reaction('🚫')
                    except:
                        pass
                audit_log(user_id, "CRITICAL_ACTION_PENDING", command=cmd_content, success=False)
                return
            
            # Exécution Directe (Install, Info, etc.)
            res = ""
            success = True
            try:
                if func_name == 'run_terminal_command': 
                    res = run_terminal_command(**args)
                elif func_name == 'modify_system_file': 
                    res = modify_system_file(**args)
                else:
                    res = "❌ Fonction inconnue."
                    success = False
            except Exception as e:
                res = f"❌ Erreur d'exécution : {str(e)}"
                success = False
                logging.error(f"Erreur exécution {func_name}: {e}")
            
            # Audit log de l'exécution
            audit_log(user_id, f"EXEC_{func_name.upper()}", command=cmd_content, result=res[:200], success=success)
            
            # Réponse finale de l'IA avec le résultat
            try:
                # Injection pour forcer la recherche PROFONDE si résultat vide ou "not found"
                analysis_prompt = f"RÉSULTAT OUTIL : {res}\n\n"
                if not res or "not found" in res.lower() or "aucun" in res.lower() or len(res) < 10 or "no such" in res.lower():
                    analysis_prompt += "[SYSTEM CRITIQUE: Le résultat est vide ou 'not found'. TU DOIS LANCER IMMÉDIATEMENT une RECHERCHE PROFONDE avec plusieurs commandes en cascade : ss -tulpn, lsof -i, ps aux, journalctl, systemctl list-units, grep dans /etc/, etc. NE DIS JAMAIS 'je ne sais pas'. Lance au moins 3-4 commandes différentes pour trouver la réponse.]"
                
                final_resp = await call_gemini_with_retry(session, analysis_prompt, user_id=user_id)
                text_to_send = final_resp.text
                
                # Vérifier si l'IA a lancé d'autres commandes dans sa réponse (recherche supplémentaire)
                try:
                    for part in final_resp.candidates[0].content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            # L'IA a lancé une autre commande de recherche, on la traite
                            func_name = part.function_call.name
                            args = dict(part.function_call.args)
                            cmd_content = args.get('command', '') or args.get('path', '')
                            
                            # Exécution directe (pas critique car recherche)
                            try:
                                if func_name == 'run_terminal_command': 
                                    res2 = run_terminal_command(**args)
                                elif func_name == 'modify_system_file': 
                                    res2 = modify_system_file(**args)
                                else:
                                    res2 = "❌ Fonction inconnue."
                                
                                audit_log(user_id, f"EXEC_FOLLOWUP_{func_name.upper()}", command=cmd_content, result=res2[:200], success=True)
                                
                                # Réponse finale avec les deux résultats
                                final_resp2 = await call_gemini_with_retry(session, f"RÉSULTAT RECHERCHE SUPPLEMENTAIRE : {res2}\n\nAnalyse les deux résultats et donne une réponse complète avec [R:SUCCESS].", user_id=user_id)
                                text_to_send = final_resp2.text
                                
                                # Afficher le résultat brut si long
                                if len(res2) > 150 or "\n" in res2:
                                    await safe_reply(update, res2, as_code=True)
                            except Exception as e:
                                logging.error(f"Erreur recherche supplémentaire: {e}")
                except (AttributeError, IndexError, KeyError):
                    # Pas de fonction call supplémentaire, on continue avec la réponse normale
                    pass
            except Exception as e:
                logging.error(f"Erreur réponse IA: {e}")
                text_to_send = f"✅ Commande exécutée.\n\n{res[:500]}"
            
            # Affichage code si log long
            display_as_code = len(res) > 150 or "\n" in res
            if display_as_code:
                await safe_reply(update, res, as_code=True)
                text_to_send = re.sub(r'\[R:.*?\]', '', text_to_send, flags=re.IGNORECASE).strip()

            # Sauvegarder la réponse du bot dans l'historique
            add_to_conversation_history(user_id, "", text_to_send)
            await process_reaction_and_send(update, text_to_send, previous_command=cmd_content)
            return

        # Pas d'outil - réponse conversationnelle
        # Sauvegarder la réponse du bot dans l'historique
        add_to_conversation_history(user_id, "", response.text)
        await process_reaction_and_send(update, response.text, previous_command="")

    except google_exceptions.ResourceExhausted:
        logging.warning("Rate limit API Gemini atteint")
        error_msg = "🤖 API momentanément surchargée. Réessaie dans quelques instants."
        add_to_conversation_history(user_id, "", error_msg)
        await process_reaction_and_send(update, error_msg)
        audit_log(user_id, "ERROR", command="API_RATE_LIMIT_FINAL", success=False)
    except google_exceptions.ServiceUnavailable:
        logging.warning("Service Gemini indisponible")
        error_msg = "🤖 Service temporairement indisponible."
        add_to_conversation_history(user_id, "", error_msg)
        await process_reaction_and_send(update, error_msg)
        audit_log(user_id, "ERROR", command="API_UNAVAILABLE_FINAL", success=False)
    except Exception as e:
        logging.error(f"FATAL: {e}", exc_info=True)
        error_msg = f"⚠️ Erreur interne : {str(e)[:200]}"
        add_to_conversation_history(user_id, "", error_msg)
        await process_reaction_and_send(update, error_msg)
        audit_log(user_id, "ERROR", command=str(e)[:200], success=False)

def detect_sentiment_from_content(text: str, previous_command: str = "") -> str:
    """Détection ULTRA-PRÉCISE du sentiment avec analyse contextuelle avancée."""
    text_lower = text.lower()
    cmd_lower = previous_command.lower() if previous_command else ""
    combined_text = text_lower + " " + cmd_lower

    # Analyse structurelle avancée
    text_length = len(text)
    has_code_block = '```' in text or '`' in text
    has_multiline = '\n' in text and text.count('\n') > 2
    word_count = len(text.split())
    has_numbers = bool(re.search(r'\d', text))
    has_paths = bool(re.search(r'/[a-zA-Z0-9/_-]+', text))

    # PRIORITÉ ABSOLUE : Détection d'erreurs renforcée
    error_indicators = [
        # Erreurs système
        'erreur', 'échec', 'échoué', 'impossible', 'interdit', '❌', '🚫', 'failed', 'error',
        'refusé', 'denied', 'permission denied', 'timeout', 'crash', 'exception', 'fatal', 'critical',
        'ne fonctionne pas', 'ne marche pas', 'bug', 'problème', 'issue', 'refused', 'cannot',
        'unable', 'invalid', 'not found', 'no such', 'missing', 'absent', 'manquant', 'inexistant',
        'command not found', 'no such file', 'permission denied', 'access denied', 'unauthorized',
        # États d'erreur spécifiques
        'inactive', 'failed', 'dead', 'stopped', 'disabled', 'masked', 'not loaded', 'not running'
    ]
    error_score = sum(2 if word in ['permission denied', 'command not found', 'not found', 'no such file'] else 1
                     for word in error_indicators if word in combined_text)
    if error_score > 0:
        return 'error'

    # PRIORITÉ 2 : Détection de succès renforcée
    success_indicators = [
        # Succès explicites
        'succès', 'réussi', 'terminé', 'fait', '✅', 'trouvé', 'découvert', 'résolu',
        'installé', 'configuré', 'activé', 'démarré', 'running', 'active', 'ok', 'done',
        'complété', 'finalisé', 'prêt', 'disponible', 'fonctionne', 'marche', 'opérationnel',
        # États positifs
        'écoute sur', 'listening', 'en cours', 'started', 'loaded', 'enabled', 'active', 'running',
        'bannies', 'correspond', 'match', 'trouvé', 'découvert', 'identifié', 'détecté', 'localisé',
        'found', 'detected', 'located', 'identified', 'successful', 'completed', 'finished',
        # Confirmation
        'oui', 'yes', 'confirmé', 'validé', 'approuvé', 'accepted', 'granted'
    ]
    success_score = sum(2 if word in ['trouvé', 'découvert', 'résolu', 'identifié', 'détecté', 'fonctionne', 'marche'] else 1
                       for word in success_indicators if word in combined_text)

    # Logique de succès améliorée
    command_was_executed = any(cmd_word in cmd_lower for cmd_word in [
        'grep', 'find', 'ss', 'ps', 'journalctl', 'systemctl', 'lsof', 'netstat', 'top', 'df',
        'free', 'apt', 'install', 'update', 'upgrade', 'mkdir', 'touch', 'echo', 'cat', 'tail'
    ])

    if success_score > 0:
        # Commande système + indicateurs de succès = SUCCESS garanti
        if command_was_executed:
            return 'success'
        # Résultat avec données concrètes + succès = SUCCESS
        if (has_numbers or has_paths) and success_score >= 1:
            return 'success'
        # Plusieurs indicateurs forts = SUCCESS
        if success_score >= 2:
            return 'success'
        # Indicateur fort seul mais avec contexte positif
        if any(word in combined_text for word in ['trouvé', 'découvert', 'résolu', 'identifié', '✅', 'ok']):
            return 'success'

    # PRIORITÉ 3 : Détection technique renforcée
    tech_keywords = [
        # Réseau
        'port', 'écoute', 'listen', 'tcp', 'udp', 'ip', 'socket', 'interface', 'route', 'dns',
        'connection', 'connexion', 'network', 'firewall', 'iptables', 'ufw', 'netstat', 'ss',
        # Système
        'processus', 'process', 'pid', 'cpu', 'memory', 'ram', 'disk', 'storage', 'load', 'uptime',
        'kernel', 'systemd', 'service', 'daemon', 'unit', 'journal', 'log', 'syslog', 'messages',
        'dmesg', 'top', 'ps', 'htop', 'df', 'free', 'du', 'mount', 'fstab', 'cron', 'systemctl',
        # Commandes
        'grep', 'find', 'locate', 'which', 'whereis', 'ls', 'll', 'la', 'pwd', 'cd', 'mkdir',
        'rmdir', 'touch', 'cat', 'tail', 'head', 'less', 'more', 'vi', 'nano', 'chmod', 'chown',
        'sudo', 'apt', 'apt-get', 'dpkg', 'snap', 'docker', 'systemctl', 'service', 'kill', 'pkill',
        # Configuration
        'config', 'conf', 'configuration', 'setting', 'parameter', '/etc/', '/var/', '/usr/',
        '/home/', '/root/', '.conf', '.ini', '.yml', '.yaml', '.json', '.xml',
        # Investigation
        'recherche', 'investigation', 'analyse', 'vérification', 'examen', 'cherche', 'fouille',
        'diagnostic', 'debug', 'troubleshoot', 'audit', 'check', 'verify', 'scan', 'monitor'
    ]
    tech_score = sum(1 for keyword in tech_keywords if keyword in combined_text)

    # Logique technique améliorée
    if command_was_executed and (tech_score >= 1 or has_code_block or has_numbers):
        return 'tech'

    if tech_score >= 3 or (has_code_block and text_length > 30) or (has_multiline and has_numbers):
        return 'tech'

    # PRIORITÉ 4 : Détection positive/amicale améliorée
    love_indicators = [
        'merci', 'bravo', 'génial', 'parfait', 'excellent', '❤️', '✨', 'super', 'cool',
        'magnifique', 'fantastique', 'remarquable', 'impressionnant', 'formidable', 'top',
        'merci beaucoup', 'parfait', 'excellent travail', 'bien joué', 'félicitations',
        'admirable', 'exceptionnel', 'brillant', 'maître', 'expert', 'pro', 'champion'
    ]
    love_score = sum(1 for word in love_indicators if word in text_lower)
    if love_score >= 1:
        return 'love'

    # PRIORITÉ 5 : Détection fun renforcée
    fun_indicators = [
        'haha', 'lol', 'mdr', '🤣', '😄', 'rigolo', 'fun', 'drôle', 'amusant', '😂', 'lol', '😆', '😊',
        'blague', 'humour', 'rire', 'sourire', '😉', '😜', '🤪', '🎪', '🎭', '🎨', '🎯'
    ]
    if any(word in text_lower for word in fun_indicators):
        return 'fun'

    # PRIORITÉ 6 : Détection motivation renforcée
    motivation_indicators = [
        'motivation', 'objectif', 'mission', 'défi', '🏆', '🎯', 'challenge', 'goal', 'défi',
        'conquérir', 'vaincre', 'réussir', 'accomplir', 'atteindre', 'progresser', 'améliorer',
        'optimiser', 'perfectionner', 'maîtriser', 'dominer', 'exceller', 'briller'
    ]
    if any(word in text_lower for word in motivation_indicators):
        return 'motivation'

    # PRIORITÉ 7 : Recherche/investigation
    investigation_indicators = [
        'recherche', 'investigation', 'analyse', 'vérification', 'examen', 'cherche', 'fouille',
        'diagnostic', 'debug', 'troubleshoot', 'audit', 'check', 'verify', 'scan', 'monitor',
        'explorer', 'explorer', 'investiguer', 'analyser', 'vérifier', 'examiner'
    ]
    if any(word in combined_text for word in investigation_indicators):
        return 'tech'

    # LOGIQUE DE FALLBACK AMÉLIORÉE
    # Résultat de commande avec données = TECH
    if previous_command and (has_numbers or has_paths or has_code_block or text_length > 50):
        return 'tech'

    # Réponses courtes positives = SUCCESS
    if text_length < 50 and any(word in text_lower for word in ['ok', 'fait', 'terminé', 'succès', 'prêt', 'disponible']):
        return 'success'

    # Réponses avec données techniques = TECH
    if has_numbers or has_code_block or has_paths:
        return 'tech'

    # Messages courts neutres = NEUTRAL
    if text_length < 100 and not any(word in text_lower for word in ['erreur', 'problème', 'succès', 'génial', 'cool']):
        return 'neutral'

    # Défaut final : NEUTRAL
    return 'neutral'

async def process_reaction_and_send(update_or_query, text, previous_command: str = ""):
    """Réactions SYSTÉMATIQUES, PERTINENTES et VRAIMENT DIVERSIFIÉES - GARANTIE D'APPLICATION."""
    # Gérer à la fois Update et CallbackQuery - TOUJOURS trouver un message
    message = None
    user_id = 0
    chat_id = None
    
    if hasattr(update_or_query, 'message') and update_or_query.message:
        message = update_or_query.message
        if hasattr(update_or_query, 'effective_user') and update_or_query.effective_user:
            user_id = update_or_query.effective_user.id
        chat_id = message.chat_id if hasattr(message, 'chat_id') else None
    elif hasattr(update_or_query, 'from_user'):  # C'est un CallbackQuery
        user_id = update_or_query.from_user.id if update_or_query.from_user else 0
        # Pour CallbackQuery, on peut réagir au message original
        if hasattr(update_or_query, 'message') and update_or_query.message:
            message = update_or_query.message
            chat_id = message.chat_id if hasattr(message, 'chat_id') else None
    elif hasattr(update_or_query, 'chat_id'):  # Message direct
        message = update_or_query
        chat_id = update_or_query.chat_id if hasattr(update_or_query, 'chat_id') else None
    
    # 1. Essayer d'extraire le tag [R:...] du texte
    reaction_key = None
    for k in REACTION_MAP.keys():
        if f"[R:{k.upper()}]" in text.upper():
            reaction_key = k
            break
    
    # 2. Détection AUTOMATIQUE et INTELLIGENTE si pas de tag
    if not reaction_key:
        # Détecter le sentiment basé sur le CONTENU de la réponse, pas sur la commande
        reaction_key = detect_sentiment_from_content(clean_text, previous_command)
        logging.info(f"🔍 Détection sentiment: {reaction_key} | réponse: {clean_text[:50]} | commande: {previous_command[:30] if previous_command else 'N/A'}")
    
    # 3. Nettoyer le texte (enlever les tags)
    clean_text = re.sub(r'\[R:.*?\]', '', text, flags=re.IGNORECASE).strip()
    
    # 4. SYSTÈME DE ROTATION ULTRA-SIMPLE ET GARANTI
    reaction_emoji = None
    reaction_applied = False

    if message:
        # Récupérer les réactions de la catégorie détectée
        all_reactions = REACTION_MAP.get(reaction_key, REACTION_MAP['neutral'])

        # Obtenir l'historique récent pour éviter la répétition
        recent_emojis = []
        if user_id in user_last_reactions:
            recent_emojis = [r[1] for r in user_last_reactions[user_id][-20:]]  # 20 dernières réactions

        # ALGORITHME DE ROTATION DÉTERMINISTE :
        # 1. Utiliser un compteur pour garantir la rotation systématique
        counter = user_reaction_counters[user_id][reaction_key]

        # 2. Préférer les réactions pas utilisées récemment
        available = [r for r in all_reactions if r not in recent_emojis[-10:]]

        # 3. Si toutes utilisées récemment, utiliser rotation complète sur toutes les réactions
        if not available:
            available = all_reactions

        # 4. Sélection déterministe avec rotation
        reaction_emoji = available[counter % len(available)]

        # 5. Incrémenter le compteur pour la prochaine fois
        user_reaction_counters[user_id][reaction_key] = (counter + 1) % len(all_reactions)
        logging.info(f"🔄 Rotation déterministe: {reaction_emoji} (catégorie: {reaction_key}, index: {counter % len(available)})")

        # Mettre à jour l'historique
        if user_id not in user_last_reactions:
            user_last_reactions[user_id] = []
        user_last_reactions[user_id].append((reaction_key, reaction_emoji))

        # Garder seulement les 30 dernières réactions
        if len(user_last_reactions[user_id]) > 30:
            user_last_reactions[user_id] = user_last_reactions[user_id][-30:]

        # APPLIQUER LA RÉACTION AVEC RETRY RENFORCÉ
        for attempt in range(5):  # 5 tentatives maximum
            try:
                await message.set_reaction(reaction_emoji)
                reaction_applied = True
                logging.info(f"✅ Réaction appliquée: {reaction_emoji} (catégorie: {reaction_key}) - succès")
                break
            except Exception as e:
                if attempt < 4:  # Pas la dernière tentative
                    await asyncio.sleep(0.3)  # Attendre un peu plus longtemps
                    logging.warning(f"Réessai réaction {attempt + 1}/5: {e}")
                else:
                    logging.error(f"Échec définitif réaction après 5 tentatives: {e}")

        # FALLBACK DE SECOURS AVEC ROTATION si toujours pas appliqué
        if not reaction_applied:
            emergency_emojis = ['✅', '👍', '👌', '🫡', '🤝', '✨', '🔍', '💡']
            # Utiliser le compteur pour faire tourner même les fallbacks
            emergency_counter = user_reaction_counters[user_id].get('emergency', 0)

            for i in range(len(emergency_emojis)):
                emoji = emergency_emojis[(emergency_counter + i) % len(emergency_emojis)]
                try:
                    await message.set_reaction(emoji)
                    reaction_applied = True
                    user_reaction_counters[user_id]['emergency'] = (emergency_counter + i + 1) % len(emergency_emojis)
                    logging.info(f"🚨 Fallback réussi avec rotation: {emoji}")
                    break
                except Exception as e:
                    logging.warning(f"Fallback {emoji} échoué: {e}")
                    continue
    
    # VÉRIFICATION FINALE : Si aucune réaction appliquée, c'est une erreur critique
    if not reaction_applied and message:
        logging.error(f"❌ CRITIQUE: Aucune réaction appliquée pour user {user_id} après tous les essais")
    
    # 5. Envoyer le message
    if clean_text: 
        await safe_reply(update_or_query, clean_text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    pending = context.user_data.get('pending')
    
    if query.data == "yes" and pending:
        try:
            await query.edit_message_text("⚙️ Exécution...")
            res = ""
            success = True
            cmd_content = pending['args'].get('command', '') or pending['args'].get('path', '')
            
            try:
                if pending['name'] == 'run_terminal_command': 
                    res = run_terminal_command(**pending['args'])
                elif pending['name'] == 'modify_system_file': 
                    res = modify_system_file(**pending['args'])
                else:
                    res = "❌ Fonction inconnue."
                    success = False
            except Exception as e:
                res = f"❌ Erreur d'exécution : {str(e)}"
                success = False
                logging.error(f"Erreur exécution confirmée {pending['name']}: {e}")
            
            # Audit log de l'exécution confirmée
            audit_log(user_id, f"EXEC_CONFIRMED_{pending['name'].upper()}", command=cmd_content, result=res[:200], success=success)
            
            # Réponse IA avec retry
            try:
                session = get_user_session(user_id)
                final = await call_gemini_with_retry(session, f"UTILISATEUR A VALIDÉ. RÉSULTAT : {res}", user_id=user_id)
                response_text = re.sub(r'\[R:.*?\]', '', final.text, flags=re.IGNORECASE).strip()
            except Exception as e:
                logging.error(f"Erreur réponse IA après confirmation: {e}")
                response_text = f"✅ Action confirmée et exécutée.\n\n{res[:500]}"
            
            # Affichage du résultat brut si long
            if len(res) > 150 or "\n" in res:
                await safe_reply(query, res, as_code=True)
            
            # Utiliser process_reaction_and_send pour les réactions
            await process_reaction_and_send(query, response_text, previous_command=cmd_content)
        except Exception as e:
            logging.error(f"Erreur dans button_handler: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Erreur lors de l'exécution : {str(e)[:200]}")
            audit_log(user_id, "ERROR", command="BUTTON_HANDLER", result=str(e)[:200], success=False)
    else:
        await query.edit_message_text("❌ Annulé.")
        # Appliquer une réaction d'annulation
        try:
            if query.message:
                await query.message.set_reaction('❌')
        except:
            try:
                if query.message:
                    await query.message.set_reaction('🚫')
            except:
                pass
        if pending:
            audit_log(user_id, "ACTION_CANCELLED", command=pending['args'].get('command', '') or pending['args'].get('path', ''), success=False)
    
    context.user_data['pending'] = None

if __name__ == "__main__":
    print("🤖 BOT V54.0 — ULTIMATE PERFECTION EN LIGNE")
    print(f"📊 Logs d'audit : {AUDIT_LOG_FILE}")
    print(f"⏱️  Timeout commandes : {COMMAND_TIMEOUT}s")
    print(f"🚦 Rate limit : {RATE_LIMIT_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    
    # Initialisation des fichiers 
    if not LAST_SEEN_FILE.exists(): 
        LAST_SEEN_FILE.touch()
    if not AUDIT_LOG_FILE.exists():
        AUDIT_LOG_FILE.touch()
    
    # Vérification des variables d'environnement
    if not TELEGRAM_TOKEN:
        print("❌ ERREUR : TELEGRAM_TOKEN manquant dans .env")
        exit(1)
    if not GEMINI_API_KEY:
        print("❌ ERREUR : GEMINI_API_KEY manquant dans .env")
        exit(1)
    if model is None:
        print("❌ ERREUR : Impossible d'initialiser le modèle Gemini")
        exit(1)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("✅ Bot démarré et prêt à recevoir des messages")
    app.run_polling()