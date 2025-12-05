// Skills API service
import api from './api';
import { Skill } from '../types';

interface SkillCreate {
  name: string;
  description: string;
  system_prompt: string;
}

export const skillsService = {
  async list(): Promise<Skill[]> {
    const response = await api.get('/skills/');
    return response.data;
  },

  async get(skillId: string): Promise<Skill> {
    const response = await api.get(`/skills/${skillId}`);
    return response.data;
  },

  async create(skill: SkillCreate): Promise<Skill> {
    const response = await api.post('/skills/', skill);
    return response.data;
  },
};
