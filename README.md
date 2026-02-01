# 🤖 Gemini Telegram Bot - Assistant IA Système

## 📋 Présentation

**Gemini Telegram Bot** est un assistant intelligent ultra-avancé qui combine les capacités de l'IA Google Gemini avec un contrôle complet du système Linux. Ce bot permet d'exécuter des commandes système, gérer des fichiers, et bénéficier d'une assistance IA conversationnelle directement depuis Telegram.

<img width="1170" height="2532" alt="image" src="https://github.com/user-attachments/assets/56f7a632-37c6-446c-bb42-613708d31fb8" />


### ✨ Fonctionnalités Principales

- **💬 Chat IA Intelligent** : Conversations naturelles avec Google Gemini 2.5 Flash
- **🖥️ Contrôle Système Complet** : Exécution de commandes Linux avec retour en temps réel
- **📁 Gestion de Fichiers** : Lecture, modification, création et suppression de fichiers
- **🔒 Sécurité Renforcée** : Système d'autorisation stricte et audit logging complet
- **⚡ Rate Limiting** : Protection contre les abus (30 requêtes/minute max)
- **📊 Historique Conversationnel** : Contexte intelligent préservé (20 derniers échanges)
- **🎭 Réactions Émotionnelles** : Système de réactions automatiques variées
- **🛡️ Gestion d'Erreurs** : Retry automatique et récupération des pannes API
- **📈 Audit Complet** : Traçabilité totale des actions et commandes

### 🏗️ Architecture

- **Backend IA** : Google Gemini 2.5 Flash avec retry automatique
- **Interface** : Telegram Bot API avec polling long-polling
- **Sécurité** : Authentification par ID utilisateur autorisé
- **Persistence** : Historique et sessions utilisateur
- **Logging** : Audit logging JSON structuré

---

## 🚀 Installation et Configuration

### 📋 Prérequis Système

**Système d'exploitation :**
- Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+)
- Python 3.8 ou supérieur
- Accès root/sudo pour l'installation des paquets

**Paquets système requis :**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git curl wget
```

### 🔧 Installation Étape par Étape

#### 1. Clonage et Préparation

```bash
# Créer le répertoire du bot
mkdir -p /home/user/bot_admin
cd /home/user/bot_admin

# Télécharger le code du bot
# (Remplacer cette section par votre méthode de déploiement)
```

#### 2. Configuration de l'Environnement Python

```bash
# Créer l'environnement virtuel
python3 -m venv venv

# Activer l'environnement virtuel
source venv/bin/activate

# Installer les dépendances Python
pip install --upgrade pip
pip install python-telegram-bot python-dotenv google-generativeai
```

#### 3. Configuration des APIs

##### a) API Google Gemini

1. **Créer un compte Google Cloud** :
   - Aller sur [Google Cloud Console](https://console.cloud.google.com/)
   - Créer un nouveau projet ou sélectionner un projet existant

2. **Activer l'API Gemini** :
   - Aller dans "APIs & Services" > "Library"
   - Rechercher "Generative Language API"
   - Cliquer sur "Enable"

3. **Créer une clé API** :
   - Aller dans "APIs & Services" > "Credentials"
   - Cliquer sur "Create credentials" > "API key"
   - Copier la clé générée

##### b) API Telegram Bot

1. **Créer le bot Telegram** :
   - Ouvrir Telegram et rechercher `@BotFather`
   - Envoyer la commande `/newbot`
   - Suivre les instructions pour nommer votre bot
   - **IMPORTANT** : Copier le token fourni par BotFather

2. **Obtenir votre Chat ID** :
   - Envoyer un message à votre bot
   - Ouvrir cette URL dans votre navigateur :
     ```
     https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates
     ```
   - Chercher "chat":{"id":XXXXXXX} dans la réponse JSON
   - Copier l'ID du chat (sera utilisé comme ALLOWED_USER_ID)

#### 4. Configuration du Bot

Créer le fichier `.env` dans le répertoire du bot :

```bash
# Créer le fichier de configuration
nano .env
```

Contenu du fichier `.env` :
```env
# Token du bot Telegram (obtenu auprès de BotFather)
TELEGRAM_TOKEN=votre_token_telegram_ici

# Clé API Google Gemini (obtenu depuis Google Cloud Console)
GEMINI_API_KEY=votre_cle_gemini_ici

# ID utilisateur Telegram autorisé (votre chat ID)
ALLOWED_USER_ID=votre_chat_id_telegram
```

#### 5. Permissions et Sécurité

```bash
# Définir le propriétaire du répertoire
sudo chown -R username:username /home/user/bot_admin

# Permissions sécurisées
chmod 700 /home/user/bot_admin
chmod 600 /home/user/bot_admin/.env
chmod 644 /home/user/bot_admin/chat-bot.py
```

#### 6. Configuration du Service Systemd (Recommandé)

Créer le fichier de service systemd :

```bash
sudo nano /etc/systemd/system/geminibot.service
```

Contenu du fichier de service :
```ini
[Unit]
Description=Gemini Telegram Bot - chat-bot.py
After=network.target

[Service]
User=username
WorkingDirectory=/home/user/bot_admin
Environment="TZ=Europe/Paris"
ExecStart=/home/user/bot_admin/venv/bin/python /home/user/bot_admin/chat-bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Activer et démarrer le service :
```bash
# Recharger systemd
sudo systemctl daemon-reload

# Activer le service au boot
sudo systemctl enable geminibot.service

# Démarrer le service
sudo systemctl start geminibot.service

# Vérifier le statut
sudo systemctl status geminibot.service
```

### 🔍 Vérification du Fonctionnement

#### Logs du Service
```bash
# Voir les logs en temps réel
sudo journalctl -u geminibot.service -f

# Voir les dernières lignes
sudo journalctl -u geminibot.service -n 20 --no-pager
```

#### Test du Bot
Envoyez un message simple à votre bot Telegram :
- `pwd` - Devrait retourner le répertoire de travail
- `ls -la` - Devrait lister les fichiers du répertoire
- `Bonjour` - Devrait répondre via l'IA Gemini

---

## 📖 Utilisation

### 💬 Commandes Système
Le bot peut exécuter n'importe quelle commande Linux :

```
ls -la /home
ps aux | grep python
df -h
```

### 🤖 Chat IA
Conversations naturelles avec l'IA :
```
Bonjour, peux-tu m'aider à comprendre Linux ?
Explique-moi comment fonctionne systemd
```

### 📁 Gestion de Fichiers
```
read /etc/hostname
write /tmp/test.txt "Hello World"
append /tmp/test.txt " - ajouté"
delete /tmp/test.txt
```

### 🎯 Fonctionnalités Avancées
- **Contexte intelligent** : L'IA se souvient des échanges précédents
- **Réactions automatiques** : Émojis adaptés au contexte
- **Rate limiting** : Protection contre les abus
- **Audit logging** : Traçabilité complète des actions

---

## 🔧 Dépannage

### Erreur "Conflict: terminated by other getUpdates request"
- **Cause** : Plusieurs instances du bot tournent simultanément
- **Solution** : Vérifier qu'une seule instance est active
```bash
sudo systemctl stop geminibot.service
pkill -f chat-bot.py
sudo systemctl start geminibot.service
```

### Erreur API Gemini
- **Cause** : Quota dépassé ou clé invalide
- **Solution** : Vérifier la clé API et le quota sur Google Cloud Console

### Bot ne répond pas
- **Cause** : Problème réseau ou token invalide
- **Solution** : Vérifier les logs et tester le token
```bash
curl https://api.telegram.org/bot<VOTRE_TOKEN>/getMe
```

### Permissions insuffisantes
- **Cause** : Droits d'accès aux fichiers ou commandes
- **Solution** : Vérifier les permissions système

---

## 📊 Monitoring et Logs

### Fichiers de Log
- **Audit log** : `.audit_log.jsonl` - Traçabilité complète des actions
- **Dernière activité** : `.last_seen` - Timestamp de dernière activité

### Commandes de Monitoring
```bash
# Logs système
sudo journalctl -u geminibot.service -f

# Vérifier les processus
ps aux | grep chat-bot

# Statut du service
sudo systemctl status geminibot.service
```

---

## 🔐 Sécurité

### Mesures de Sécurité Implémentées
- **Authentification stricte** : Un seul utilisateur autorisé (ALLOWED_USER_ID)
- **Rate limiting** : Maximum 30 requêtes par minute
- **Audit logging** : Traçabilité complète des actions
- **Validation des chemins** : Protection contre path traversal
- **Timeout des commandes** : Prévention des commandes bloquantes (90s max)
- **Échappement sécurisé** : Protection contre injection de commandes

### Recommandations de Sécurité
- Changer régulièrement les tokens API
- Surveiller les logs d'audit
- Utiliser des permissions minimales
- Mettre à jour régulièrement les dépendances

---

## 📝 Support et Contribution

### Signaler un Bug
1. Vérifier les logs du service
2. Tester avec une commande simple
3. Fournir les logs d'erreur complets

### Mises à Jour
- Surveiller les logs pour les avertissements de dépréciation
- Mettre à jour régulièrement les dépendances Python
- Tester les nouvelles versions en environnement de développement

---

## 📄 Licence

Ce projet est fourni tel quel, sans garantie. Utilisez à vos propres risques.

**Version actuelle :** Beta 1 (Gemini Telegram Bot - Assistant IA Système)

---
*Dernière mise à jour : Février 2026*
