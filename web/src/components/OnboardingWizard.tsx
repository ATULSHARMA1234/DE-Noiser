'use client';

import React, { useState, useEffect } from 'react';
import { Cpu, Play, Sparkles, Shield, ArrowRight, X } from 'lucide-react';

export function OnboardingWizard() {
  const [isOpen, setIsOpen] = useState(false);
  const [step, setStep] = useState(1);

  useEffect(() => {
    const completed = localStorage.getItem('onboarding_completed');
    if (!completed) {
      setIsOpen(true);
    }
  }, []);

  const handleNext = () => {
    if (step < 4) {
      setStep(prev => prev + 1);
    } else {
      handleClose();
    }
  };

  const handleClose = () => {
    localStorage.setItem('onboarding_completed', 'true');
    setIsOpen(false);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-md z-[500] flex items-center justify-center p-4">
      <div className="bg-[#0f0f13] border border-white/10 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl relative transition-all duration-300">
        
        {/* Top fuchsia line */}
        <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-fuchsia-600 to-violet-600"></div>

        {/* Close Button */}
        <button 
          onClick={handleClose}
          className="absolute top-4 right-4 text-zinc-500 hover:text-white transition-colors cursor-pointer border-none bg-transparent"
        >
          <X size={18} />
        </button>

        {/* Wizard Content */}
        <div className="p-8">
          {step === 1 && (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="w-12 h-12 rounded-xl bg-fuchsia-500/10 border border-fuchsia-500/20 flex items-center justify-center text-fuchsia-400">
                <Cpu size={24} />
              </div>
              <h2 className="text-xl font-bold text-white">Welcome to SemanticOS Platform</h2>
              <p className="text-xs text-zinc-400 leading-relaxed">
                SemanticOS is an autonomous, private-by-default SRE copilot. It automatically ingests million-line log streams, normalizes timestamps, redacts PII data, groups anomalies, and maps drift, backed by high-fidelity local LLM triaging.
              </p>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400">
                <Play size={24} />
              </div>
              <h2 className="text-xl font-bold text-white">Upload & Provision Log Sources</h2>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Connect log feeds from standard folders or tap Kubernetes clusters, AWS CloudWatch, and Docker containers directly using our sandbox and integrations directories.
              </p>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="w-12 h-12 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center text-violet-400">
                <Sparkles size={24} />
              </div>
              <h2 className="text-xl font-bold text-white">Neural HDBSCAN Clustering</h2>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Launch neural analysis engines on your sources. HDBSCAN collapses raw noise by up to 99%, extracting core causal graphs, drift report summaries, and remediation recipes.
              </p>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                <Shield size={24} />
              </div>
              <h2 className="text-xl font-bold text-white">Secure Audit & Alerts Routing</h2>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Establish Slack channels or SMTP alerts, browse outbound logs under the Alerts History, and inspect strict SOC2-compliant mutative operations inside the Security Audit Trail.
              </p>
            </div>
          )}

          {/* Dots Indicator + Action Footer */}
          <div className="flex items-center justify-between pt-8 mt-6 border-t border-white/5">
            <div className="flex gap-1.5">
              {[1, 2, 3, 4].map(s => (
                <div 
                  key={s} 
                  className={`h-1.5 rounded-full transition-all duration-300 ${s === step ? 'w-4 bg-fuchsia-500' : 'w-1.5 bg-white/10'}`} 
                />
              ))}
            </div>
            
            <button
              onClick={handleNext}
              className="bg-fuchsia-600 hover:bg-fuchsia-500 text-white font-bold rounded-lg px-4 py-2 text-xs flex items-center gap-1.5 cursor-pointer transition-colors border-none"
            >
              {step === 4 ? 'Get Started' : 'Next'} <ArrowRight size={14} />
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}
