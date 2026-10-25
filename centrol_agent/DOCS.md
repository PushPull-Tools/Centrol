# Centrol Agent

Permet à [Centrol](https://github.com/PushPull-Tools/Centrol) de surveiller la machine Home Assistant OS :
processeur, mémoire, température, disques, réseau, conteneurs des modules (statistiques, journaux) et processus.
**Lecture seule** : l'agent ne peut rien démarrer, arrêter ni modifier.

## Installation

1. Ajoutez le dépôt `https://github.com/PushPull-Tools/Centrol` (Paramètres › Modules complémentaires › Boutique › ⋮ › Dépôts).
2. Installez **Centrol Agent**.
3. Désactivez **Mode protection** (nécessaire pour lire Docker et les processus de la machine), puis **Démarrer**.
4. Ouvrez l'onglet **Journal** : il affiche l'adresse et le jeton.
5. Dans Centrol : **Paramètres › Serveurs › Ajouter un serveur**, collez l'adresse `http://<IP de Home Assistant>:8323` et le jeton.

## Options

- **Jeton** : vide = créé tout seul au premier démarrage et gardé ensuite (affiché dans le journal).
- **Port** : 8323 par défaut.

## Sécurité

Le jeton protège chaque requête. N'ouvrez pas ce port sur Internet : passez par le réseau local ou le VPN.
