/**
 * Updates Service - App update check, download, and install API
 */
import { API_BASE_URL } from './api';

export interface UpdateInfo {
  current_version: string;
  latest_version?: string;
  update_available: boolean;
  release_notes?: string;
  download_url?: string;
  asset_download_url?: string;
  error?: string;
}

export interface DownloadProgress {
  downloading: boolean;
  progress: number;
  message: string;
  error?: string;
}

class UpdatesService {
  async checkForUpdates(): Promise<UpdateInfo> {
    const response = await fetch(`${API_BASE_URL}/updates/check`);
    if (!response.ok) throw new Error('Failed to check for updates');
    return response.json();
  }

  async downloadAndInstall(): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/updates/download-and-install`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to start download');
    return response.json();
  }

  async getDownloadProgress(): Promise<DownloadProgress> {
    const response = await fetch(`${API_BASE_URL}/updates/download-progress`);
    if (!response.ok) throw new Error('Failed to get download progress');
    return response.json();
  }

  async installAndRestart(): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/updates/install-and-restart`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to install update');
    return response.json();
  }
}

export const updatesService = new UpdatesService();
