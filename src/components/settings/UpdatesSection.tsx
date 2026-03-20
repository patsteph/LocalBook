import React, { useState, useEffect } from 'react';
import { updatesService } from '../../services/updates';
import type { SourceInfo } from '../../services/updates';

export const UpdatesSection: React.FC = () => {
    const [updateInfo, setUpdateInfo] = useState<{
        current_version: string;
        latest_version?: string;
        update_available: boolean;
        release_notes?: string;
        download_url?: string;
        asset_download_url?: string;
        error?: string;
    } | null>(null);
    const [sourceInfo, setSourceInfo] = useState<SourceInfo | null>(null);
    const [checkingUpdates, setCheckingUpdates] = useState(false);
    const [updateMessage, setUpdateMessage] = useState<string | null>(null);
    const [upgrading, setUpgrading] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);

    useEffect(() => {
        checkForUpdates();
        loadSourceInfo();
    }, []);

    const loadSourceInfo = async () => {
        try {
            const info = await updatesService.getSourceInfo();
            setSourceInfo(info);
        } catch {
            setSourceInfo({ has_source: false });
        }
    };

    const checkForUpdates = async () => {
        setCheckingUpdates(true);
        setUpdateMessage(null);
        try {
            const data = await updatesService.checkForUpdates();
            setUpdateInfo(data);
        } catch (err) {
            setUpdateInfo({
                current_version: '0.1.0',
                update_available: false,
                error: 'Could not connect to server'
            });
        } finally {
            setCheckingUpdates(false);
        }
    };

    const handleUpgrade = async () => {
        setUpgrading(true);
        setUpdateMessage(null);
        setShowConfirm(false);

        try {
            const result = await updatesService.launchUpgrade();
            setUpdateMessage(result.message);

            if (result.success) {
                // Give Terminal a moment to open, then quit the app
                setTimeout(async () => {
                    try {
                        const { exit } = await import('@tauri-apps/plugin-process');
                        await exit(0);
                    } catch (e) {
                        console.error('Failed to exit app:', e);
                        setUpdateMessage('Upgrade is running in Terminal. Please quit LocalBook manually.');
                        setUpgrading(false);
                    }
                }, 2000);
            } else {
                setUpgrading(false);
            }
        } catch (err) {
            setUpdateMessage(err instanceof Error ? err.message : 'Failed to launch upgrade');
            setUpgrading(false);
        }
    };

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-base font-semibold text-gray-900 dark:text-white mb-1">Software Updates</h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                    Check for and install updates from GitHub.
                </p>
            </div>

            {/* Current Version */}
            <div className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                <div className="flex items-center justify-between">
                    <div>
                        <h4 className="text-sm font-medium text-gray-900 dark:text-white">Current Version</h4>
                        <p className="text-lg font-bold text-blue-600 dark:text-blue-400 mt-1">
                            v{updateInfo?.current_version || '...'}
                        </p>
                    </div>
                    <button
                        onClick={checkForUpdates}
                        disabled={checkingUpdates}
                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {checkingUpdates ? 'Checking...' : 'Check for Updates'}
                    </button>
                </div>
            </div>

            {/* Update Status */}
            {updateInfo && (
                <div className={`p-4 border rounded-lg ${
                    updateInfo.update_available 
                        ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/20'
                        : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
                }`}>
                    {updateInfo.error ? (
                        <div className="text-red-600 dark:text-red-400">
                            <p className="font-medium">Error checking for updates</p>
                            <p className="text-sm mt-1">{updateInfo.error}</p>
                        </div>
                    ) : updateInfo.update_available ? (
                        <div>
                            <div className="flex items-center gap-2 mb-3">
                                <span className="text-green-600 dark:text-green-400 text-xl">🎉</span>
                                <h4 className="font-medium text-green-700 dark:text-green-300">
                                    Update Available: v{updateInfo.latest_version}
                                </h4>
                            </div>
                            {updateInfo.release_notes && (
                                <div className="mb-4 p-3 bg-white dark:bg-gray-800 rounded-lg text-sm text-gray-600 dark:text-gray-400">
                                    <p className="font-medium text-gray-900 dark:text-white mb-1">Release Notes:</p>
                                    <p className="whitespace-pre-wrap">{updateInfo.release_notes}</p>
                                </div>
                            )}

                            {/* Upgrade Actions */}
                            {sourceInfo?.has_source ? (
                                // Source-based upgrade via install.sh
                                <div className="space-y-3">
                                    {!showConfirm && !upgrading && (
                                        <div className="flex gap-3">
                                            <button
                                                onClick={() => setShowConfirm(true)}
                                                className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                                            >
                                                <span>🚀</span> Upgrade Now
                                            </button>
                                            {updateInfo.download_url && (
                                                <a
                                                    href={updateInfo.download_url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                                >
                                                    View on GitHub →
                                                </a>
                                            )}
                                        </div>
                                    )}

                                    {/* Confirmation */}
                                    {showConfirm && !upgrading && (
                                        <div className="p-3 border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 rounded-lg">
                                            <p className="text-sm font-medium text-amber-800 dark:text-amber-200 mb-2">
                                                This will close LocalBook and open Terminal to run the upgrade.
                                            </p>
                                            <p className="text-sm text-amber-700 dark:text-amber-300 mb-3">
                                                The upgrade pulls the latest code, rebuilds the app, and relaunches automatically. Your data is safe.
                                            </p>
                                            <div className="flex gap-2">
                                                <button
                                                    onClick={handleUpgrade}
                                                    className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors"
                                                >
                                                    Yes, Upgrade
                                                </button>
                                                <button
                                                    onClick={() => setShowConfirm(false)}
                                                    className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                                >
                                                    Cancel
                                                </button>
                                            </div>
                                        </div>
                                    )}

                                    {/* Upgrading state */}
                                    {upgrading && (
                                        <div className="flex items-center gap-3 p-3 border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                                            <div className="animate-spin h-4 w-4 border-2 border-blue-600 border-t-transparent rounded-full" />
                                            <p className="text-sm text-blue-700 dark:text-blue-300">
                                                Opening Terminal... LocalBook will close in a moment.
                                            </p>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                // No source install — show GitHub link
                                <div className="space-y-3">
                                    <p className="text-sm text-gray-600 dark:text-gray-400">
                                        To update, re-run the installer command in Terminal:
                                    </p>
                                    <div className="p-2 bg-gray-900 dark:bg-gray-950 rounded-lg font-mono text-xs text-green-400 overflow-x-auto">
                                        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/{`patsteph`}/LocalBook/master/install.sh)"
                                    </div>
                                    {updateInfo.download_url && (
                                        <a
                                            href={updateInfo.download_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-block px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                        >
                                            View on GitHub →
                                        </a>
                                    )}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                            <span className="text-xl">✅</span>
                            <p>You're running the latest version!</p>
                        </div>
                    )}
                </div>
            )}

            {/* Update Message */}
            {updateMessage && (
                <div className="p-4 border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                    <p className="text-blue-700 dark:text-blue-300">{updateMessage}</p>
                </div>
            )}

            {/* Install Info */}
            {sourceInfo?.has_source && (
                <div className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
                    <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-1">📁 Source Install</h4>
                    <p className="text-sm text-gray-600 dark:text-gray-400">
                        Installed from source at <span className="font-mono text-xs">{sourceInfo.install_dir}</span>
                    </p>
                </div>
            )}

            {/* Data Safety Note */}
            <div className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
                <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-1">💾 Your Data is Safe</h4>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                    All your notebooks, sources, and settings are stored separately from the app. 
                    Updates will never affect your data.
                </p>
            </div>
        </div>
    );
};
