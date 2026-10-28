// Adresse du backend.
// - Web sur le même ordinateur : http://localhost:8000
// - Téléphone (Expo Go) : remplacer par l'adresse IP de l'ordinateur, ex. http://192.168.1.20:8000
export const API_URL = "http://localhost:8000";
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";
