/**
 * People Service - People profiler, members, and social auth API
 */
import { API_BASE_URL } from './api';

class PeopleService {
  async getConfig(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/config`);
    if (!response.ok) throw new Error('Failed to fetch people config');
    return response.json();
  }

  async updateConfig(notebookId: string, config: any): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!response.ok) throw new Error('Failed to update people config');
    return response.json();
  }

  async toggleCoaching(notebookId: string, enabled: boolean): Promise<any> {
    const response = await fetch(
      `${API_BASE_URL}/people/${notebookId}/config/coaching?enabled=${enabled}`,
      { method: 'PATCH' }
    );
    if (!response.ok) throw new Error('Failed to toggle coaching');
    return response.json();
  }

  async getMembers(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members`, {
      method: 'GET',
    });
    if (!response.ok) throw new Error('Failed to fetch members');
    return response.json();
  }

  async getMember(notebookId: string, memberId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}`);
    if (!response.ok) throw new Error('Failed to fetch member');
    return response.json();
  }

  async deleteMember(notebookId: string, memberId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete member');
  }

  async collectAll(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/collect-all`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to start collection');
    return response.json();
  }

  async collectMember(notebookId: string, memberId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/collect`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to collect member data');
    return response.json();
  }

  async getMemberActivity(notebookId: string, memberId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/activity`);
    if (!response.ok) throw new Error('Failed to fetch activity');
    return response.json();
  }

  async getMemberInsights(notebookId: string, memberId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/insights`);
    if (!response.ok) throw new Error('Failed to fetch insights');
    return response.json();
  }

  async getMemberCoaching(notebookId: string, memberId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/coaching`);
    if (!response.ok) throw new Error('Failed to fetch coaching');
    return response.json();
  }

  async getAuthStatus(): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/auth/status`);
    if (!response.ok) throw new Error('Failed to fetch auth status');
    return response.json();
  }

  async authenticate(platform: string, credentials: any): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/auth/${platform}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(credentials),
    });
    if (!response.ok) throw new Error('Failed to authenticate');
    return response.json();
  }

  async addNote(notebookId: string, memberId: string, text: string, category: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, category }),
    });
    if (!response.ok) throw new Error('Failed to add note');
    return response.json();
  }

  async deleteNote(notebookId: string, memberId: string, noteId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/notes/${noteId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete note');
  }

  async addGoal(notebookId: string, memberId: string, goal: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/goals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal }),
    });
    if (!response.ok) throw new Error('Failed to add goal');
    return response.json();
  }

  async deleteGoal(notebookId: string, memberId: string, goalId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/people/${notebookId}/members/${memberId}/goals/${goalId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete goal');
  }
}

export const peopleService = new PeopleService();
