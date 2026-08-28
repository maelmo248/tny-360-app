# 🚀 Guide d'installation et de lancement du projet

Ce projet est composé d'une application TypeScript qui orchestre et lance automatiquement un serveur Python (YOLO) en arrière-plan.

## 🛠️ 1. Prérequis

Avant de commencer, vérifiez que ces outils sont installés sur votre machine :
* **Git** : Pour télécharger le code source.
* **Python** : Lors de l'installation sous Windows, assurez-vous que l'option **"Add Python to PATH"** est bien cochée.
* **Node.js** : Indispensable pour exécuter l'environnement TypeScript (inclut le gestionnaire de paquets `npm`).

## 📦 2. Récupération du projet

Ouvrez un terminal (ou le terminal intégré de VS Code) et téléchargez le projet via Git :

```bash
git clone https://github.com/maelmo248/tny-360-app.git
cd tny-360-app
```

## 🐍 3. Installation des dépendances Python

Le serveur Python a besoin de ses bibliothèques (notamment Ultralytics pour YOLO) pour fonctionner. Déplacez-vous dans le dossier du serveur et installez-les directement :

```bash
cd serveur-yolo
pip install -r requirements.txt
cd ..
```
*(Note : Le modèle de détection `yolo26n.pt` n'est pas versionné. Il sera téléchargé automatiquement par la librairie lors de la première exécution s'il n'est pas déjà présent dans le dossier).*

## 💻 4. Installation et exécution globale

Puisque TypeScript se charge de démarrer le serveur Python, il vous suffit de préparer et lancer l'application principale depuis la racine du projet (`tny-360-app`) :

```bash
# 1. Installer les modules Node.js
npm install

# 2. Démarrer le projet (Client TypeScript + Serveur Python)
npx vite
```
*(Si votre script de démarrage dans `package.json` est différent, utilisez `npm start` ou la commande appropriée).*

## 🔄 Utilisation au quotidien

Pour vos prochaines sessions, il ne sera plus nécessaire de réinstaller toutes les dépendances. Ouvrez simplement un terminal à la racine du projet et tapez :

```bash
npx vite
```
