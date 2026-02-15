import React, { useState, useEffect } from 'react';
import { updatesService } from '../../services/updates';

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
    const [checkingUpdates, setCheckingUpdates] = useState(false);
    const [updateMessage, setUpdateMessage] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<{
        downloading: boolean;
        progress: number;
        message: string;
        error?: string;
    } | null>(null);
    const [readyToInstall, setReadyToInstall] = useState(false);

    useEffect(() => {
        checkForUpdates();
    }, []);

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

    const downloadAndInstall = async () => {
        setUpdateMessage(null);
        setDownloadProgress({ downloading: true, progress: 0, message: 'Starting download...' });
        setReadyToInstall(false);
        
        try {
            const response = await updatesService.downloadAndInstall();
            
            const pollProgress = async () => {
                try {
                    const progress = await updatesService.getDownloadProgress();
                    setDownloadProgress(progress);
                    
                    if (progress.error) {
                        setUpdateMessage(`Error: ${progress.error}`);
                        return false;
                    }
                    
                    if (progress.progress >= 100) {
                        setReadyToInstall(true);
                        return false;
                    }
                    
                    return progress.downloading;
                } catch {
                    return false;
                }
            };
            
            while (await pollProgress()) {
                await new Promise(resolve => setTimeout(resolve, 500));
            }
            
            if (response.success) {
                setUpdateMessage(response.message);
                setReadyToInstall(true);
            } else {
                setUpdateMessage(response.message);
                setDownloadProgress(null);
            }
            
        } catch (err) {
            setUpdateMessage(err instanceof Error ? err.message : 'Download failed');
            setDownloadProgress(null);
        }
    };

    const installAndRestart = async () => {
        setUpdateMessage('Installing update...');
        try {
            const result = await updatesService.installAndRestart();
            setUpdateMessage(result.message);
            
            if (result.success) {
                setTimeout(async () => {
                    try {
                        const { exit } = await import('@tauri-apps/plugin-process');
                        await exit(0);
                    } catch (e) {
                        console.error('Failed to exit app:', e);
                        setUpdateMessage('Please manually quit and reopen the app to complete the update.');
                    }
                }, 1000);
            } else {
                setUpdateMessage('Failed to install update');
            }
        } catch (err) {
            setUpdateMessage(err instanceof Error ? err.message : 'Installation failed');
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Software Updates</h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
                    Check for and install updates from GitHub.
                </p>
            </div>

            {/* Current Version */}
            <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                <div className="flex items-center justify-between">
                    <div>
                        <h4 className="font-medium text-gray-900 dark:text-white">Current Version</h4>
                        <p className="text-2xl font-bold text-blue-600 dark:text-blue-400 mt-1">
                            v{updateInfo?.current_version || '0.1.0'}
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
                                <div className="mb-4 p-3 bg-white dark:bg-gray-800 rounded text-sm text-gray-600 dark:text-gray-400">
                                    <p className="font-medium text-gray-900 dark:text-white mb-1">Release Notes:</p>
                                    <p className="whitespace-pre-wrap">{updateInfo.release_notes}</p>
                                </div>
                            )}
                            {/* Download Progress */}
                            {downloadProgress && downloadProgress.downloading && (
                                <div className="mb-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="text-sm text-gray-600 dark:text-gray-400">{downloadProgress.message}</span>
                                        <span className="text-sm font-medium text-gray-900 dark:text-white">{downloadProgress.progress}%</span>
                                    </div>
                                    <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                                        <div 
                                            className="bg-green-600 h-2 rounded-full transition-all duration-300"
                                            style={{ width: `${downloadProgress.progress}%` }}
                                        />
                                    </div>
                                </div>
                            )}
                            
                            <div className="flex gap-3">
                                {readyToInstall ? (
                                    <button
                                        onClick={installAndRestart}
                                        className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                                    >
                                        <span>🚀</span> Install & Restart
                                    </button>
                                ) : (
                                    <button
                                        onClick={downloadAndInstall}
                                        disabled={downloadProgress?.downloading}
                                        className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-green-400 text-white rounded-lg text-sm font-medium transition-colors"
                                    >
                                        {downloadProgress?.downloading ? 'Downloading...' : 'Download & Install'}
                                    </button>
                                )}
                                {updateInfo.download_url && !downloadProgress?.downloading && !readyToInstall && (
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

            {/* Data Safety Note */}
            <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
                <h4 className="font-medium text-gray-900 dark:text-white mb-2">💾 Your Data is Safe</h4>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                    All your notebooks, sources, and settings are stored separately from the app. 
                    Updates will never affect your data.
                </p>
            </div>
        </div>
    );
};
