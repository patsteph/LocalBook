/**
 * Site Credentials Service
 * 
 * Frontend service for managing encrypted site credentials.
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface SiteCredential {
  site_domain: string;
  site_name: string;
  username: string;
  login_method: string;
  created_at: string;
  last_used?: string;
  notes?: string;
}

export interface AddCredentialRequest {
  site_domain: string;
  site_name: string;
  username: string;
  password: string;
  login_method?: string;
  notes?: string;
}

export const credentialService = {
  /**
   * List all stored credentials (passwords not returned)
   */
  async list(): Promise<SiteCredential[]> {
    const response = await fetch(`${API_BASE}/credentials/`);
    if (!response.ok) throw new Error('Failed to list credentials');
    return response.json();
  },

  /**
   * Add or update a credential
   */
  async add(request: AddCredentialRequest): Promise<SiteCredential> {
    const response = await fetch(`${API_BASE}/credentials/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error('Failed to add credential');
    return response.json();
  },

  /**
   * Delete a credential
   */
  async delete(siteDomain: string): Promise<void> {
    const response = await fetch(`${API_BASE}/credentials/${encodeURIComponent(siteDomain)}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete credential');
  },

  /**
   * Check if a credential exists
   */
  async exists(siteDomain: string): Promise<boolean> {
    const response = await fetch(`${API_BASE}/credentials/${encodeURIComponent(siteDomain)}/exists`);
    if (!response.ok) return false;
    const data = await response.json();
    return data.exists;
  },

  /**
   * Test a credential
   */
  async test(siteDomain: string): Promise<{ success: boolean; message: string }> {
    const response = await fetch(`${API_BASE}/credentials/${encodeURIComponent(siteDomain)}/test`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to test credential');
    return response.json();
  },

  /**
   * Get disclaimer text
   */
  async getDisclaimer(): Promise<{ title: string; message: string }> {
    const response = await fetch(`${API_BASE}/credentials/disclaimer`);
    if (!response.ok) throw new Error('Failed to get disclaimer');
    return response.json();
  },
};
