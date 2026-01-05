// API service for backend communication
import axios from 'axios';

export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const WS_BASE_URL = (() => {
  try {
    const url = new URL(API_BASE_URL);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString().replace(/\/$/, '');
  } catch {
    return API_BASE_URL.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
  }
})();

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 5 minute timeout for long operations (uploads, concept extraction)
});

// Request interceptor to set correct Content-Type
api.interceptors.request.use((config) => {
  // Let axios set the Content-Type automatically for FormData (includes boundary)
  // Only set application/json for non-FormData requests
  if (!(config.data instanceof FormData)) {
    config.headers['Content-Type'] = 'application/json';
  }
  return config;
});

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

export default api;
