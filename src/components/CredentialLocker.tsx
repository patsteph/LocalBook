/**
 * Credential Locker Component
 * 
 * Manage encrypted site credentials for authenticated web scraping.
 */

import React, { useState, useEffect } from 'react';
import { credentialService, SiteCredential } from '../services/credentials';

export const CredentialLocker: React.FC = () => {
  const [credentials, setCredentials] = useState<SiteCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [showDisclaimer, setShowDisclaimer] = useState(false);
  const [disclaimerAccepted, setDisclaimerAccepted] = useState(
    localStorage.getItem('credential_disclaimer_accepted') === 'true'
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form state
  const [formData, setFormData] = useState({
    site_domain: '',
    site_name: '',
    username: '',
    password: '',
    notes: '',
  });

  useEffect(() => {
    loadCredentials();
  }, []);

  const loadCredentials = async () => {
    try {
      setLoading(true);
      const creds = await credentialService.list();
      setCredentials(creds);
    } catch (err) {
      console.error('Failed to load credentials:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAcceptDisclaimer = () => {
    localStorage.setItem('credential_disclaimer_accepted', 'true');
    setDisclaimerAccepted(true);
    setShowDisclaimer(false);
  };

  const handleAddClick = () => {
    if (!disclaimerAccepted) {
      setShowDisclaimer(true);
    } else {
      setShowAddForm(true);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.site_domain || !formData.username || !formData.password) {
      setError('Please fill in all required fields');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await credentialService.add({
        site_domain: formData.site_domain,
        site_name: formData.site_name || formData.site_domain,
        username: formData.username,
        password: formData.password,
        notes: formData.notes || undefined,
      });

      setSuccess('Credential saved successfully');
      setFormData({ site_domain: '', site_name: '', username: '', password: '', notes: '' });
      setShowAddForm(false);
      await loadCredentials();
      
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save credential');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (siteDomain: string) => {
    if (!window.confirm(`Delete credential for ${siteDomain}?`)) return;

    try {
      await credentialService.delete(siteDomain);
      setSuccess('Credential deleted');
      await loadCredentials();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete credential');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
          Site Login Credentials
        </h3>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Store login credentials for paywalled sites. Credentials are encrypted locally.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {success && (
        <div className="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded text-sm text-green-700 dark:text-green-400">
          {success}
        </div>
      )}

      {/* Disclaimer Modal */}
      {showDisclaimer && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-gray-800 rounded-lg p-6 max-w-md mx-4 shadow-xl">
            <h4 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              ⚠️ Important Notice
            </h4>
            <div className="text-sm text-gray-600 dark:text-gray-400 space-y-3">
              <p>
                This feature stores site credentials locally on YOUR device for personal research use.
              </p>
              <ul className="list-disc list-inside space-y-1">
                <li>Credentials are encrypted and stored only locally</li>
                <li>No credentials are transmitted to any cloud service</li>
                <li>You are responsible for complying with each site's Terms of Service</li>
                <li>Use this feature responsibly and legally</li>
              </ul>
              <p className="font-medium text-gray-800 dark:text-gray-200">
                By continuing, you acknowledge these terms.
              </p>
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowDisclaimer(false)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                onClick={handleAcceptDisclaimer}
                className="flex-1 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded text-sm font-medium"
              >
                I Understand
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add Credential Form */}
      {showAddForm && (
        <form onSubmit={handleSubmit} className="p-4 border-2 border-amber-200 dark:border-amber-700 rounded-lg bg-amber-50 dark:bg-amber-900/10">
          <h4 className="font-medium text-gray-900 dark:text-white mb-4">Add Site Credential</h4>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Site Domain *
              </label>
              <input
                type="text"
                value={formData.site_domain}
                onChange={(e) => setFormData({ ...formData, site_domain: e.target.value })}
                placeholder="medium.com"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Display Name
              </label>
              <input
                type="text"
                value={formData.site_name}
                onChange={(e) => setFormData({ ...formData, site_name: e.target.value })}
                placeholder="Medium"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Email / Username *
              </label>
              <input
                type="text"
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                placeholder="user@example.com"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Password *
              </label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                placeholder="••••••••"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Notes (optional)
              </label>
              <input
                type="text"
                value={formData.notes}
                onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                placeholder="Any notes about this account"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
              />
            </div>
          </div>

          <div className="flex gap-2 mt-4">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-gray-400 text-white rounded text-sm font-medium"
            >
              {saving ? 'Saving...' : 'Save Credential'}
            </button>
          </div>
        </form>
      )}

      {/* Credentials List */}
      <div className="space-y-3">
        {credentials.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
            <p className="text-lg mb-2">🔐</p>
            <p className="text-sm">No site credentials stored yet.</p>
            <p className="text-xs mt-1">Add credentials to access paywalled content.</p>
          </div>
        ) : (
          credentials.map((cred) => (
            <div
              key={cred.site_domain}
              className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 flex items-center justify-between"
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-900 dark:text-white">
                    {cred.site_name}
                  </span>
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    ({cred.site_domain})
                  </span>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  {cred.username}
                </p>
                {cred.last_used && (
                  <p className="text-xs text-gray-400 mt-1">
                    Last used: {new Date(cred.last_used).toLocaleDateString()}
                  </p>
                )}
              </div>
              <button
                onClick={() => handleDelete(cred.site_domain)}
                className="px-3 py-1.5 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-sm"
              >
                Delete
              </button>
            </div>
          ))
        )}
      </div>

      {/* Add Button */}
      {!showAddForm && (
        <button
          onClick={handleAddClick}
          className="w-full py-3 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-600 dark:text-gray-400 hover:border-amber-400 hover:text-amber-600 dark:hover:text-amber-400 transition-colors"
        >
          + Add Site Credential
        </button>
      )}

      {/* Security Note */}
      <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <p className="text-xs text-gray-500 dark:text-gray-400">
          🔒 <strong>Security:</strong> Credentials are encrypted with AES-128 and stored locally. 
          They never leave your device. You're responsible for site ToS compliance.
        </p>
      </div>
    </div>
  );
};
